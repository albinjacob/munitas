import { useState } from "react";

/** A choice kept across visits. Where the browser will not store it, the page still works. */
export function useStored<T extends string>(key: string, initial: () => T): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => {
    try {
      return (localStorage.getItem(key) as T | null) ?? initial();
    } catch {
      return initial();
    }
  });
  const set = (v: T) => {
    setValue(v);
    try {
      localStorage.setItem(key, v);
    } catch {
      /* the choice just lasts until the page is closed */
    }
  };
  return [value, set];
}

/** Light or dark: what the person chose, else what their system prefers. */
export function useTheme() {
  return useStored<"light" | "dark">("munitas-theme", () =>
    typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light",
  );
}
