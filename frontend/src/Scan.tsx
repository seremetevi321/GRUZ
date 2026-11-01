import { useRef, useState } from "react";
import { api, DocType, OrderDetail, ScanField, ScanInfo, ScanResult } from "./api";

export const DOC_TITLE: Record<DocType, string> = { waybill: "Накладная (ТТН)", trip_sheet: "Путевой лист" };

const FIELD_LABELS: Record<DocType, [string, string][]> = {
  waybill: [["number", "Номер ТТН"], ["date", "Дата"], ["trip_sheet_number", "К путевому листу №"], ["driver_name", "Водитель"],
    ["driver_license", "Удостоверение"], ["vehicle_model", "Автомобиль"], ["vehicle_plate", "Госномер"]],
  trip_sheet: [["number", "Номер"], ["date", "Дата"], ["driver_name", "Водитель"], ["driver_license", "Удостоверение"],
    ["vehicle_model", "Марка автомобиля"], ["vehicle_plate", "Госномер"], ["vehicle_type", "Тип ТС"], ["medical_check", "Медосмотр"]],
};

const STATUS_TEXT = { ok: "распознано", check: "проверьте", missing: "не найдено" } as const;
const ACCEPT = "image/jpeg,image/png,application/pdf";

/** Блок перевозчика: загрузка фото или скана документа и подача после проверки. */
export function DocumentUpload({ order, run, busy }: { order: OrderDetail; run: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const [scan, setScan] = useState<ScanResult | null>(null);
  const [loading, setLoading] = useState<DocType | null>(null);
  const [err, setErr] = useState("");
  const inputs = { waybill: useRef<HTMLInputElement>(null), trip_sheet: useRef<HTMLInputElement>(null) };

  const pick = async (type: DocType, file: File | undefined) => {
    if (!file) return;
    setErr(""); setLoading(type);
    try { setScan(await api.upload<ScanResult>(`/api/orders/${order.id}/scans`, file, { doc_type: type })); }
    catch (e) { setErr((e as Error).message); }
    finally { setLoading(null); }
  };

  return (
    <div className="card">
      <h3>Документы рейса <span className="tag">фото, скан или PDF — поля заполнятся сами</span></h3>
      <div className="doc-slots">
        {(["waybill", "trip_sheet"] as DocType[]).map((t) => {
          const doc = order.documents[t];
          return (
            <div key={t} className={"slot" + (doc ? " filled" : "")}>
              <div>
                <b>{DOC_TITLE[t]}</b>
                <div className="muted small">{doc ? `№${doc.number}${doc._version > 1 ? ` · версия ${doc._version}` : ""} подан` : "ещё не подан"}</div>
              </div>
              <input ref={inputs[t]} type="file" accept={ACCEPT} hidden onChange={(e) => { pick(t, e.target.files?.[0]); e.target.value = ""; }} />
              <button disabled={busy || loading !== null} onClick={() => inputs[t].current?.click()}>
                {loading === t ? "Распознаём…" : doc ? "Загрузить исправленный" : "Загрузить фото или скан"}
              </button>
            </div>
          );
        })}
      </div>
      {loading && <div className="muted small scanning">Выравниваем изображение, распознаём текст и ищем поля бланка — обычно 3–10 секунд</div>}
      {err && <div className="alert block">{err}</div>}
      {scan && <ScanReview order={order} scan={scan} onClose={() => setScan(null)} run={run} busy={busy} />}
    </div>
  );
}

function initial(scan: ScanResult): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k] of FIELD_LABELS[scan.doc_type]) {
    const v = scan.fields[k]?.value;
    out[k] = v === null || v === undefined ? (k === "vehicle_type" ? "truck" : k === "medical_check" ? "false" : "") : String(v);
  }
  return out;
}

function ScanReview({ order, scan, onClose, run, busy }: { order: OrderDetail; scan: ScanResult; onClose: () => void; run: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const [vals, setVals] = useState(() => initial(scan));
  const [hover, setHover] = useState<string | null>(null);
  const [touched, setTouched] = useState<Set<string>>(new Set());
  const refs = useRef<Record<string, HTMLInputElement | HTMLSelectElement | null>>({});
  const labels = FIELD_LABELS[scan.doc_type];

  const set = (k: string, v: string) => { setVals({ ...vals, [k]: v }); setTouched(new Set(touched).add(k)); };
  const missingRequired = ["number", "driver_name"].filter((k) => !vals[k]?.trim());

  const submit = () => run(async () => {
    const data: Record<string, unknown> = {};
    for (const [k] of labels) {
      const v = vals[k]?.trim() ?? "";
      if (k === "medical_check") data[k] = v === "true";
      else if (v) data[k] = v;
    }
    await api.act(`/api/orders/${order.id}/documents`, { doc_type: scan.doc_type, scan_id: scan.scan_id, [scan.doc_type]: data });
    onClose();
  });

  const counts = labels.reduce((a, [k]) => { a[scan.fields[k]?.status ?? "missing"]++; return a; }, { ok: 0, check: 0, missing: 0 });

  return (
    <div className="scan-review">
      <div className="scan-head">
        <div>
          <b>{DOC_TITLE[scan.doc_type]}: проверьте распознанные поля</b>
          <div className="muted small">
            распознано {counts.ok} · проверить {counts.check} · не найдено {counts.missing} · {(scan.elapsed_ms / 1000).toFixed(1)} с
          </div>
        </div>
        <button className="ghost" onClick={onClose}>Закрыть</button>
      </div>
      {scan.warnings.map((w, i) => <div key={i} className="alert warning">{w}</div>)}
      <div className="scan-grid">
        <div className="scan-image">
          <img src={scan.preview} alt="Загруженный документ" />
          {labels.map(([k, t]) => {
            const f = scan.fields[k];
            if (!f?.box) return null;
            const [x0, y0, x1, y1] = f.box;
            return (
              <button key={k} type="button" title={t} className={`box b-${f.status}` + (hover === k ? " on" : "")}
                style={{ left: `${x0 * 100}%`, top: `${y0 * 100}%`, width: `${(x1 - x0) * 100}%`, height: `${(y1 - y0) * 100}%` }}
                onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)} onClick={() => refs.current[k]?.focus()} />
            );
          })}
        </div>
        <div className="scan-fields">
          {labels.map(([k, t]) => {
            const f: ScanField = scan.fields[k] ?? { value: null, confidence: 0, status: "missing", box: null, source: null };
            const st = touched.has(k) ? "edited" : f.status;
            return (
              <label key={k} className={`sf s-${st}` + (hover === k ? " on" : "")} onMouseEnter={() => setHover(k)} onMouseLeave={() => setHover(null)}>
                <span className="sf-head">{t}<em>{touched.has(k) ? "исправлено" : STATUS_TEXT[f.status]}{f.status !== "missing" && !touched.has(k) ? ` · ${Math.round(f.confidence * 100)}%` : ""}</em></span>
                {k === "vehicle_type" ? (
                  <select ref={(el) => { refs.current[k] = el; }} value={vals[k]} onChange={(e) => set(k, e.target.value)}>
                    <option value="truck">грузовой</option><option value="car">легковой</option>
                  </select>
                ) : k === "medical_check" ? (
                  <select ref={(el) => { refs.current[k] = el; }} value={vals[k]} onChange={(e) => set(k, e.target.value)}>
                    <option value="true">пройден</option><option value="false">нет отметки</option>
                  </select>
                ) : (
                  <input ref={(el) => { refs.current[k] = el; }} value={vals[k]} onChange={(e) => set(k, e.target.value)} placeholder="не найдено — введите вручную" />
                )}
                {f.source && !touched.has(k) && <span className="sf-src">в документе: «{f.source}»</span>}
              </label>
            );
          })}
          {missingRequired.length > 0 && <div className="alert warning">Заполните номер документа и водителя</div>}
          <button className="primary" disabled={busy || missingRequired.length > 0} onClick={submit}>Подтвердить и подать</button>
          <div className="muted small">Банк сохранит оригинал файла и отметит, какие поля вы исправили вручную.</div>
        </div>
      </div>
    </div>
  );
}

/** Кнопка «Оригинал»: показывает загруженный файл любому участнику заявки. */
export function OriginalButton({ scan }: { scan: ScanInfo }) {
  const [url, setUrl] = useState<string | null>(null);
  const open = async () => {
    const b = await api.blob(`/api/scans/${scan.id}/file`);
    const u = URL.createObjectURL(b);
    if (scan.mime === "application/pdf") {
      const a = document.createElement("a"); a.href = u; a.download = `скан-${scan.id}.pdf`; a.click();
      setTimeout(() => URL.revokeObjectURL(u), 10000);
    } else setUrl(u);
  };
  const corrected = scan.corrections ? Object.keys(scan.corrections).length : 0;
  return (
    <>
      <button className="link small" onClick={open}>оригинал{corrected ? ` · исправлено полей: ${corrected}` : ""}</button>
      {url && (
        <div className="viewer" onClick={() => { URL.revokeObjectURL(url); setUrl(null); }}>
          <img src={url} alt="Оригинал документа" />
          <span>Нажмите, чтобы закрыть</span>
        </div>
      )}
    </>
  );
}
