import { OriginConfig, DestinationConfig, WorkflowStatus } from "./api";

const ORIGINS_KEY = "me-origins";
const DESTINATION_KEY = "me-destination";
const RUN_ID_KEY = "me-active-run-id";
const STATUS_KEY = "me-pipeline-status";
const TAB_KEY = "me-active-tab";

export function loadOriginsFromStorage(): OriginConfig[] {
  try {
    const raw = localStorage.getItem(ORIGINS_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch {
    return [];
  }
}

export function saveOriginsToStorage(origins: OriginConfig[]): void {
  try {
    localStorage.setItem(ORIGINS_KEY, JSON.stringify(origins));
  } catch {}
}

export function loadDestinationFromStorage(): DestinationConfig | null {
  try {
    const raw = localStorage.getItem(DESTINATION_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveDestinationToStorage(destination: DestinationConfig | null): void {
  try {
    if (destination) {
      localStorage.setItem(DESTINATION_KEY, JSON.stringify(destination));
    } else {
      localStorage.removeItem(DESTINATION_KEY);
    }
  } catch {}
}

export function loadRunIdFromStorage(): string {
  return localStorage.getItem(RUN_ID_KEY) || "";
}

export function saveRunIdToStorage(runId: string): void {
  if (runId) {
    localStorage.setItem(RUN_ID_KEY, runId);
  } else {
    localStorage.removeItem(RUN_ID_KEY);
  }
}

export function loadStatusFromStorage(): WorkflowStatus | null {
  try {
    const raw = localStorage.getItem(STATUS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function saveStatusToStorage(status: WorkflowStatus | null): void {
  try {
    if (status) {
      localStorage.setItem(STATUS_KEY, JSON.stringify(status));
    } else {
      localStorage.removeItem(STATUS_KEY);
    }
  } catch {}
}

export function loadActiveTabFromStorage(): string {
  return localStorage.getItem(TAB_KEY) || "setup";
}

export function saveActiveTabToStorage(tab: string): void {
  localStorage.setItem(TAB_KEY, tab);
}
