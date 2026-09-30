import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { BrowserRouter } from "react-router-dom";
import App from "./App.tsx";
import { IdentityProvider } from "./identity/IdentityContext";
import "./index.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      // A governance console should not retry a denial. A 403 is an answer, and
      // retrying it three times turns one audited decision into four.
      retry: (failureCount, error) => {
        const status = (error as { status?: number })?.status;
        if (status && status >= 400 && status < 500) return false;
        return failureCount < 2;
      },
      staleTime: 10_000,
    },
  },
});

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <IdentityProvider>
        <BrowserRouter>
          <App />
        </BrowserRouter>
      </IdentityProvider>
    </QueryClientProvider>
  </StrictMode>,
);
