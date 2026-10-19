import type { RendererApi } from "../preload/api";

declare global {
  interface Window {
    shuliu: RendererApi;
  }
}

export {};
