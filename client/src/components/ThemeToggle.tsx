import { useEffect, useState } from "react";

type Theme = "dark" | "light";

// The pre-paint script in index.html already set data-theme on <html>; start
// from whatever it decided so the button matches the rendered UI on first paint.
function initial(): Theme {
  return document.documentElement.getAttribute("data-theme") === "light" ? "light" : "dark";
}

// A single floating toggle. Each view renders full-screen with no shared chrome,
// so this lives once at the App root and pins to the corner over everything.
export default function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>(initial);

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    localStorage.setItem("theme", theme);
  }, [theme]);

  const next: Theme = theme === "dark" ? "light" : "dark";
  return (
    <button
      type="button"
      onClick={() => setTheme(next)}
      title={`Switch to ${next} mode`}
      aria-label={`Switch to ${next} mode`}
      // bg-zinc-100 is the OPPOSITE of the page (zinc-950): light circle on the
      // dark theme, dark circle on the light theme — always high-contrast, never
      // camouflaged against the background it floats over.
      className="fixed bottom-4 right-4 z-50 flex h-11 w-11 items-center justify-center rounded-full bg-zinc-100 text-lg text-zinc-900 shadow-lg ring-1 ring-zinc-900/10 transition hover:bg-zinc-200"
    >
      {theme === "dark" ? "☀️" : "🌙"}
    </button>
  );
}
