import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode, useState } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Route, Routes } from "react-router";
import AccessGate from "./components/AccessGate";
import Layout from "./components/Layout";
import { ToastProvider } from "./components/toast";
import "./index.css";
import { ApiError, getAccessCode } from "./lib/api";
import Dashboard from "./pages/Dashboard";
import Expenses from "./pages/Expenses";
import ModelCalls from "./pages/ModelCalls";
import NotFound from "./pages/NotFound";
import ReceiptDetail from "./pages/ReceiptDetail";
import Receipts from "./pages/Receipts";
import ReviewQueue from "./pages/ReviewQueue";
import UploadPage from "./pages/Upload";

function isAccessError(error: unknown) {
  return error instanceof ApiError && error.code === "access_code_required";
}

function App() {
  const [locked, setLocked] = useState(false);
  const [client] = useState(() => {
    const onError = (error: unknown) => { if (isAccessError(error)) setLocked(true); };
    return new QueryClient({
      queryCache: new QueryCache({ onError }),
      mutationCache: new MutationCache({ onError }),
      defaultOptions: {
        queries: {
          staleTime: 5_000,
          retry: (count, error) => !(error instanceof ApiError && error.status >= 400 && error.status < 500) && count < 2,
        },
      },
    });
  });

  if (locked) {
    return <AccessGate invalid={!!getAccessCode()} onUnlock={() => { setLocked(false); client.resetQueries(); }} />;
  }
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>
        <BrowserRouter>
          <Routes>
            <Route element={<Layout />}>
              <Route index element={<Dashboard />} />
              <Route path="upload" element={<UploadPage />} />
              <Route path="review" element={<ReviewQueue />} />
              <Route path="receipts" element={<Receipts />} />
              <Route path="receipts/:id" element={<ReceiptDetail />} />
              <Route path="expenses" element={<Expenses />} />
              <Route path="model-calls" element={<ModelCalls />} />
              <Route path="*" element={<NotFound />} />
            </Route>
          </Routes>
        </BrowserRouter>
      </ToastProvider>
    </QueryClientProvider>
  );
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
