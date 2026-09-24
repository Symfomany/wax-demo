(() => {
  // Boutons de copie des blocs de code
  document.querySelectorAll("pre").forEach((pre) => {
    const button = document.createElement("button");
    button.className = "copy";
    button.type = "button";
    button.textContent = "Copier";
    button.addEventListener("click", async () => {
      const text = pre.querySelector("code")?.innerText ?? pre.innerText;
      try {
        await navigator.clipboard.writeText(text.replace(/\s+$/, ""));
        button.textContent = "Copié";
      } catch {
        button.textContent = "Sélectionner ↑";
      }
      setTimeout(() => (button.textContent = "Copier"), 1500);
    });
    pre.appendChild(button);
  });

  // Thème : clair / auto / sombre
  const root = document.documentElement;
  const buttons = document.querySelectorAll("[data-set-theme]");
  const apply = (mode) => {
    if (mode === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", mode);
    buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.setTheme === mode)));
    try { localStorage.setItem("llm-harness-theme", mode); } catch {}
  };
  let saved = "system";
  try { saved = localStorage.getItem("llm-harness-theme") || "system"; } catch {}
  apply(saved);
  buttons.forEach((b) => b.addEventListener("click", () => apply(b.dataset.setTheme)));

  // Section active dans le sommaire
  const links = new Map([...document.querySelectorAll(".toc a")].map((a) => [a.hash.slice(1), a]));
  const observer = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        links.forEach((a) => a.classList.remove("active"));
        links.get(entry.target.id)?.classList.add("active");
      }
    });
  }, { rootMargin: "-20% 0px -70% 0px" });
  document.querySelectorAll("main section[id]").forEach((s) => observer.observe(s));
})();
