import { useState } from "react";

// Scaffolded by `fastplace make:hook useDebounce` — shared hooks live under
// resources/js/hooks/; import them from pages, layouts, or components.
export function useDebounce(initial = null) {
  const [value, setValue] = useState(initial);

  // TODO: build the hook's API and return it.
  return [value, setValue];
}
