// Initialise mermaid for Material's instant-loading nav and respect the
// theme palette. Re-runs after every page swap so diagrams render on
// nav-tab changes too.
document.addEventListener("DOMContentLoaded", () => {
  const isDark = document.body.dataset.mdColorScheme === "slate";
  mermaid.initialize({
    startOnLoad: true,
    theme: isDark ? "dark" : "default",
    securityLevel: "loose",
  });
});

// Material theme palette toggle — re-render diagrams in the new theme.
document.querySelectorAll('[data-md-color-scheme]').forEach((el) => {
  new MutationObserver(() => {
    const isDark = document.body.dataset.mdColorScheme === "slate";
    document.querySelectorAll(".mermaid").forEach((node) => {
      // Strip any rendered SVG so mermaid re-runs on the source.
      if (node.dataset.processed === "true") {
        node.removeAttribute("data-processed");
        node.innerHTML = node.dataset.source || node.innerHTML;
      } else {
        node.dataset.source = node.innerHTML;
      }
    });
    mermaid.initialize({
      startOnLoad: false,
      theme: isDark ? "dark" : "default",
      securityLevel: "loose",
    });
    mermaid.run();
  }).observe(el, { attributes: true, attributeFilter: ["data-md-color-scheme"] });
});
