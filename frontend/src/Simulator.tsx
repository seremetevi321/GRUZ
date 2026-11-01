import { useState } from "react";
import { api, OrderDetail } from "./api";

const SAMPLE_WAYBILL = { number: "512", trip_sheet_number: "150", driver_name: "Иванов Ю.И.", driver_license: "57МК № 113540", vehicle_model: "КамАЗ 53-215", vehicle_plate: "X133MT77RUS" };
const SAMPLE_SHEET = { number: "150", driver_name: "Иванов Юрий Иванович", driver_license: "57МК113540", vehicle_model: "КамАЗ 53-215", vehicle_plate: "Х 133 МТ 77", vehicle_type: "truck", medical_check: true };

const STEPS: [string, string][] = [
  ["departed", "Выезд"], ["arrived_loading", "Прибытие на погрузку"], ["departed_loading", "Убытие с погрузки"],
  ["arrived_unloading", "Прибытие на выгрузку"], ["departed_unloading", "Убытие с выгрузки"], ["delivered", "Доставка подтверждена"],
];

/** Кнопки вместо ЭДО, электронных путевых листов и телематики. В пилоте те же события придут от адаптеров. */
export function Simulator({ order, run, busy }: { order: OrderDetail; run: (fn: () => Promise<unknown>) => void; busy: boolean }) {
  const [wb, setWb] = useState<Record<string, any>>({ ...SAMPLE_WAYBILL, number: `ТТН-${order.id}` });
  const [ts, setTs] = useState<Record<string, any>>({ ...(order.documents.trip_sheet ?? SAMPLE_SHEET) });
  const [loading, setLoading] = useState("90");
  const [unloading, setUnloading] = useState("60");
  const done = new Set(order.events.map((e) => e.kind));
  const p = `/api/orders/${order.id}/events`;

  const base = new Date(); base.setHours(7, 0, 0, 0);
  const at = (min: number) => new Date(base.getTime() + min * 60000).toISOString();
  const L = Math.max(0, Number(loading) || 0), U = Math.max(0, Number(unloading) || 0);
  const times: Record<string, string> = {
    departed: at(0), arrived_loading: at(60), departed_loading: at(60 + L),
    arrived_unloading: at(180 + L), departed_unloading: at(180 + L + U), delivered: at(185 + L + U),
  };
  const id = (k: string) => `${order.id}-${k}-${crypto.randomUUID()}`;
  const send = (kind: string) => api.event(p, { event_id: id(kind), kind, occurred_at: times[kind] });
  const sendWaybill = () => api.event(p, { event_id: id("wb"), kind: "waybill_signed", occurred_at: at(-60), waybill: wb });
  const sendSheet = () => api.event(p, { event_id: id("ts"), kind: "trip_sheet_issued", occurred_at: at(-50), trip_sheet: ts });
  const allTrack = async () => {
    if (!order.documents.waybill) await sendWaybill();
    if (!order.documents.trip_sheet) await sendSheet();
    for (const [k] of STEPS) if (!done.has(k)) await send(k);
  };

  const field = (doc: Record<string, any>, set: (d: Record<string, any>) => void, k: string, t: string) =>
    <label key={k}>{t}<input value={doc[k] ?? ""} onChange={(e) => set({ ...doc, [k]: e.target.value })} /></label>;

  return (
    <div className="card sim">
      <h3>Симулятор внешних систем <span className="tag">ЭДО · путевые листы · трек</span></h3>
      <div className="grid2">
        <fieldset>
          <legend>Накладная подписана (ЭДО)</legend>
          {field(wb, setWb, "number", "Номер ТТН")}
          {field(wb, setWb, "trip_sheet_number", "К путевому листу №")}
          {field(wb, setWb, "driver_name", "Водитель")}
          {field(wb, setWb, "driver_license", "Удостоверение")}
          {field(wb, setWb, "vehicle_plate", "Госномер")}
          <button disabled={busy} onClick={() => run(sendWaybill)}>{order.documents.waybill ? "Подать накладную ещё раз" : "Подписать накладную"}</button>
        </fieldset>
        <fieldset>
          <legend>Путевой лист выпущен (ЭПЛ)</legend>
          {field(ts, setTs, "number", "Номер")}
          {field(ts, setTs, "driver_name", "Водитель")}
          {field(ts, setTs, "driver_license", "Удостоверение")}
          {field(ts, setTs, "vehicle_plate", "Госномер")}
          <label className="check"><input type="checkbox" checked={!!ts.medical_check} onChange={(e) => setTs({ ...ts, medical_check: e.target.checked })} /> Медосмотр пройден</label>
          <div className="preset">
            <button type="button" className="link" onClick={() => setTs({ ...ts, driver_name: "Петров П.П.", driver_license: "77АА 000001" })}>другой водитель</button>
            <button type="button" className="link" onClick={() => setTs({ ...ts, driver_name: SAMPLE_SHEET.driver_name, driver_license: SAMPLE_SHEET.driver_license })}>вернуть водителя из ТТН</button>
          </div>
          <button disabled={busy} onClick={() => run(sendSheet)}>{order.documents.trip_sheet ? "Подать исправленный путевой лист" : "Выпустить путевой лист"}</button>
        </fieldset>
      </div>
      <div className="track">
        <label className="inline">Погрузка, мин <input value={loading} onChange={(e) => setLoading(e.target.value)} /></label>
        <label className="inline">Выгрузка, мин <input value={unloading} onChange={(e) => setUnloading(e.target.value)} /></label>
        <span className="muted small">норма 120 мин, сверх нормы — 1 000 ₽/ч</span>
      </div>
      <div className="steps">
        {STEPS.map(([k, t]) => <button key={k} className={done.has(k) ? "done" : ""} disabled={busy || done.has(k)} onClick={() => run(() => send(k))}>{done.has(k) ? "✓ " : ""}{t}</button>)}
        <button className="primary" disabled={busy || STEPS.every(([k]) => done.has(k))} onClick={() => run(allTrack)}>Весь трек разом</button>
      </div>
    </div>
  );
}
