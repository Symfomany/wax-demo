"""Appels LLM structurés : schéma JSON imposé, validation Pydantic, cache, budget."""

import hashlib
import json
import re
import threading
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app import storage
from app.config import settings


T = TypeVar("T", bound=BaseModel)

# (messages, json_schema) -> texte brut renvoyé par le modèle
InvokeFn = Callable[[list[dict[str, str]], dict[str, Any]], str]


class BudgetExceeded(RuntimeError):
    pass


class LLMOutputError(RuntimeError):
    pass


def inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Remplace les $ref/$defs Pydantic par leur définition (grammaire Ollama)."""
    definitions = schema.get("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(definitions[node["$ref"].split("/")[-1]])
            return {key: resolve(value) for key, value in node.items() if key != "$defs"}
        if isinstance(node, list):
            return [resolve(value) for value in node]
        return node

    return resolve(schema)


def extract_json(text: str) -> str:
    """Tolère les blocs ```json``` et le texte parasite autour de l'objet."""
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    return text[start : end + 1] if start != -1 and end > start else text.strip()


class StructuredLLM:
    def __init__(
        self,
        invoke: InvokeFn,
        model: str,
        connection=None,
        max_calls: int = 20,
    ) -> None:
        self._invoke = invoke
        self.model = model
        self.connection = connection
        self.max_calls = max_calls
        self.calls = 0
        self.cache_hits = 0
        self._lock = threading.Lock()  # Scouts parallèles

    def generate(self, prompt: str, schema: type[T], max_attempts: int = 2) -> T:
        json_schema = inline_refs(schema.model_json_schema())
        key = hashlib.sha256(
            json.dumps([self.model, json_schema, prompt], sort_keys=True).encode()
        ).hexdigest()

        if self.connection is not None:
            cached = storage.cache_get(self.connection, key)
            if cached is not None:
                with self._lock:
                    self.cache_hits += 1
                return schema.model_validate_json(cached)

        messages = [{"role": "user", "content": prompt}]
        last_error: Exception | None = None

        for _ in range(max_attempts):
            with self._lock:
                if self.calls >= self.max_calls:
                    raise BudgetExceeded(
                        f"Budget de {self.max_calls} appels LLM atteint pour ce run."
                    )
                self.calls += 1
            if getattr(self._invoke, "wants_schema_class", False):
                raw = self._invoke(messages, json_schema, schema=schema)
            else:
                raw = self._invoke(messages, json_schema)

            try:
                result = schema.model_validate_json(extract_json(raw))
            except ValidationError as error:
                last_error = error
                messages += [
                    {"role": "assistant", "content": raw},
                    {
                        "role": "user",
                        "content": (
                            "Ta réponse ne respecte pas le schéma JSON :\n"
                            f"{error}\nCorrige et renvoie uniquement le JSON."
                        ),
                    },
                ]
                continue

            if self.connection is not None:
                storage.cache_set(self.connection, key, self.model, result.model_dump_json())
            return result

        raise LLMOutputError(f"Sortie {schema.__name__} invalide : {last_error}")


def ollama_invoke() -> InvokeFn:
    # Les callbacks Langfuse sont passés à graph.invoke : LangChain les
    # propage automatiquement aux appels ChatOllama faits dans les nœuds.
    from langchain_ollama import ChatOllama

    def invoke(messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str:
        chat = ChatOllama(
            model=settings.llm_model,
            base_url=settings.ollama_url,
            temperature=settings.llm_temperature,
            num_ctx=settings.llm_num_ctx,
            format=json_schema,
            client_kwargs={"timeout": settings.llm_timeout},
        )
        response = chat.invoke(messages, config={"run_name": json_schema.get("title", "llm")})
        return response.content

    return invoke


def openai_temperature(value: float) -> float | None:
    """Pas de température pour Claude : les modèles récents la refusent (même via la couche OpenAI)."""
    return None if settings.llm_model.startswith("claude") else value


def schema_instruction(json_schema: dict[str, Any]) -> str:
    return (
        "\n\nRéponds uniquement avec un objet JSON conforme à ce schéma, sans texte autour :\n"
        + json.dumps(json_schema, ensure_ascii=False)
    )


def openai_invoke() -> InvokeFn:
    """Serveur compatible OpenAI (Ollama /v1, vLLM, LM Studio… ou la couche de
    compatibilité OpenAI de l'API Claude).

    `response_format` est envoyé (respecté par Ollama et vLLM) ET le schéma est
    rappelé dans le prompt : la couche de compatibilité de Claude ignore
    `response_format`. La validation Pydantic + relance de StructuredLLM reste
    la garantie finale.
    """
    from langchain_openai import ChatOpenAI

    def invoke(messages: list[dict[str, str]], json_schema: dict[str, Any]) -> str:
        chat = ChatOpenAI(
            model=settings.llm_model,
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key,
            temperature=openai_temperature(settings.llm_temperature),
            timeout=settings.llm_timeout,
            max_retries=2,
        )
        prompt = [dict(m) for m in messages]
        prompt[0]["content"] += schema_instruction(json_schema)
        response = chat.invoke(
            prompt,
            response_format={
                "type": "json_schema",
                "json_schema": {"name": json_schema.get("title", "output"), "schema": json_schema},
            },
            config={"run_name": json_schema.get("title", "llm")},
        )
        return response.text

    return invoke


# Fallbacks côté serveur : un refus est relancé automatiquement sur un autre modèle.
ANTHROPIC_FALLBACK_BETA = "server-side-fallback-2026-07-01"


def anthropic_invoke() -> InvokeFn:
    """API Claude native (SDK officiel `anthropic`) : Structured Outputs.

    `messages.parse(output_format=Modèle)` contraint la réponse au schéma
    Pydantic ; pas de `temperature` (refusée par les modèles Claude récents).
    """
    import anthropic

    client = anthropic.Anthropic(
        api_key=settings.anthropic_api_key,  # None : variables d'environnement / profil `ant`
        timeout=float(settings.llm_timeout),
        max_retries=2,
    )

    def invoke(messages: list[dict[str, str]], json_schema: dict[str, Any], schema=None) -> str:
        response = client.beta.messages.parse(
            model=settings.llm_model,
            max_tokens=16000,
            messages=messages,
            output_format=schema,
            betas=[ANTHROPIC_FALLBACK_BETA],
            fallbacks="default",
        )
        if response.stop_reason == "refusal":
            raise LLMOutputError(f"Refus du modèle : {response.stop_details}")
        if response.parsed_output is not None:
            return response.parsed_output.model_dump_json()
        # max_tokens atteint… : le texte brut part en validation (relance avec l'erreur)
        return "".join(block.text for block in response.content if block.type == "text")

    invoke.wants_schema_class = True
    return invoke


PROVIDERS = {"ollama": ollama_invoke, "openai": openai_invoke, "anthropic": anthropic_invoke}


def get_llm(connection=None) -> StructuredLLM:
    return StructuredLLM(
        PROVIDERS[settings.llm_provider](),
        # Nom inchangé pour Ollama : le cache SQLite existant reste valable.
        model=settings.llm_model if settings.llm_provider == "ollama"
        else f"{settings.llm_provider}:{settings.llm_model}",
        connection=connection,
        max_calls=settings.max_llm_calls,
    )


def get_chat_model():
    """Modèle conversationnel (streaming) du chat, selon le fournisseur configuré."""
    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=settings.llm_model,
            max_tokens=16000,
            anthropic_api_key=settings.anthropic_api_key,
            default_request_timeout=float(settings.llm_timeout),
        )
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(
            model=settings.llm_model, base_url=settings.llm_base_url, api_key=settings.llm_api_key,
            temperature=openai_temperature(0.3), timeout=settings.llm_timeout,
        )
    from langchain_ollama import ChatOllama

    return ChatOllama(
        model=settings.llm_model, base_url=settings.ollama_url, temperature=0.3,
        num_ctx=settings.llm_num_ctx, client_kwargs={"timeout": settings.llm_timeout},
    )
