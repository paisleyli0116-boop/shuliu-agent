import { contextBridge, ipcRenderer } from "electron";
import { exposeRendererApi } from "./api.js";

exposeRendererApi(contextBridge, ipcRenderer);
