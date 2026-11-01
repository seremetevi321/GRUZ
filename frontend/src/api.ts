// Токен сессии живёт только в памяти окна: не пишется ни в localStorage, ни на диск.
// Закрыли окно — сессия на этом устройстве забыта.
const BASE: string = (import.meta.env.VITE_API_URL as string | undefined) ?? "";
let token: string | null = null;

export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string) { super(message); }
}

async function call<T>(method: string, path: string, body?: unknown, idempotent = false): Promise<T> {
  const headers: Record<string, string> = {};
  if (token) headers["Authorization"] = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  if (idempotent) headers["Idempotency-Key"] = crypto.randomUUID();
  let res: Response;
  try {
    res = await fetch(BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
  } catch {
    throw new ApiError(0, "network", "Сервер недоступен. Проверьте, что он запущен.");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    if (res.status === 401) token = null;
    throw new ApiError(res.status, data.error ?? "error", data.message ?? `Ошибка ${res.status}`);
  }
  return data as T;
}

export const api = {
  async login(login: string, password: string) {
    const r = await call<{ token: string }>("POST", "/api/auth/login", { login, password });
    token = r.token;
  },
  async logout() { try { await call("POST", "/api/auth/logout"); } finally { token = null; } },
  get: <T,>(path: string) => call<T>("GET", path),
  async upload<T>(path: string, file: File, fields: Record<string, string> = {}): Promise<T> {
    const fd = new FormData();
    fd.append("file", file);
    for (const [k, v] of Object.entries(fields)) fd.append(k, v);
    let res: Response;
    try {
      res = await fetch(BASE + path, { method: "POST", body: fd, headers: token ? { Authorization: `Bearer ${token}` } : {} });
    } catch {
      throw new ApiError(0, "network", "Сервер недоступен. Проверьте, что он запущен.");
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new ApiError(res.status, data.error ?? "error", data.message ?? `Ошибка ${res.status}`);
    return data as T;
  },
  async blob(path: string): Promise<Blob> {
    const res = await fetch(BASE + path, { headers: token ? { Authorization: `Bearer ${token}` } : {} });
    if (!res.ok) throw new ApiError(res.status, "error", "Не удалось получить файл");
    return res.blob();
  },
  act: <T = unknown,>(path: string, body: unknown = {}) => call<T>("POST", path, body, true),
  event: <T = unknown,>(path: string, body: unknown) => call<T>("POST", path, body),
};

export type Role = "customer" | "carrier" | "operator";

export interface Me {
  id: number; login: string; full_name: string; role: Role; company_id: number; company_name: string;
  balances: { available: number; reserved: number; invoice: number }; demo_mode: boolean;
}

export interface Order {
  id: number; customer_id: number; carrier_id: number; customer_name: string; carrier_name: string;
  route_from: string; route_to: string; cargo: string; price: number; buffer: number; reserve_total: number;
  status: string; status_title: string; proposed_price: number | null; agreed_price: number | null;
  downtime_comp: number; is_duplicate: boolean; created_at: string;
}

export interface Finding {
  rule: string; severity: "ok" | "info" | "warning" | "block"; document: string | null; field: string | null;
  message: string; guilty: "customer" | "carrier" | null; amount: number | null;
}

export interface Decision { id: number; order_id: number | null; actor: string; kind: string; amount: number | null; message: string; created_at: string; }

export type DocType = "waybill" | "trip_sheet";

export interface ScanField { value: string | boolean | null; confidence: number; status: "ok" | "check" | "missing"; box: [number, number, number, number] | null; source: string | null; }

export interface ScanResult {
  scan_id: number; doc_type: DocType; detected_type: DocType | null; type_confidence: number;
  fields: Record<string, ScanField>; warnings: string[]; preview: string; preview_size: [number, number]; elapsed_ms: number;
}

export interface ScanInfo { id: number; doc_type: DocType; mime: string; uploaded_by: string; confirmed_at: string; corrections: Record<string, { recognized: unknown; confirmed: unknown }> | null; }

export interface OrderDetail extends Order {
  reserve_balance: number;
  documents: { waybill: Record<string, any> | null; trip_sheet: Record<string, any> | null };
  events: { kind: string; title: string; occurred_at: string }[];
  findings: Finding[];
  decisions: Decision[];
  scans: ScanInfo[];
}

export interface Metrics {
  avg_agree_to_pay_seconds: number | null; confirmed_share: number | null; confirmed: number; reached_delivery: number;
  blocked_count: number; blocked_sum: number; dispute_share: number | null; disputes: number;
  downtime_comp_sum: number; duplicates: number;
}

export interface Invariants { ok: boolean; client_money: number; funded: number; sum_all_accounts: number; unbalanced_txns: number; accounts_out_of_sync: number; }

export const rub = (kop: number | null | undefined) =>
  kop == null ? "—" : (kop / 100).toLocaleString("ru-RU", { minimumFractionDigits: kop % 100 ? 2 : 0, maximumFractionDigits: 2 }) + " ₽";

/** «120 000» или «120000,50» → копейки. null, если ввод некорректен. */
export function parseRub(s: string): number | null {
  const n = Number(s.replace(/[\s ]/g, "").replace(",", "."));
  if (!Number.isFinite(n) || n <= 0 || n > 1e10) return null;
  return Math.round(n * 100);
}
