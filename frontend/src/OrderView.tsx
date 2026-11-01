import { useCallback, useState } from "react";
import { api, Finding, Me, OrderDetail, parseRub, rub, ScanInfo } from "./api";
import { DocumentUpload, OriginalButton } from "./Scan";
import { usePoll } from "./App";
import { Simulator } from "./Simulator";
import { DecisionList } from "./Feed";

const SEV_ICON = { ok: "✓", info: "i", warning: "!", block: "✕" } as const;
const GUILTY = { customer: "заказчик", carrier: "перевозчик" } as const;

export function OrderView({ id, me, onChanged }: { id: number; me: Me; onChanged: () => void }) {
  const [o, setO] = useState<OrderDetail | null>(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setO(await api.get<OrderDetail>(`/api/orders/${id}`)); }
    catch (e) { setErr((e as Error).message); }
  }, [id]);
  usePoll(load);

  const run = async (fn: () => Promise<unknown>) => {
    setBusy(true); setErr("");
    try { await fn(); } catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); await load(); onChanged(); }
  };

  if (!o) return <div className="card">{err || "Загрузка…"}</div>;
  const blocks = o.findings.filter((f) => f.severity === "block");
  const simStatuses = ["accepted", "in_transit", "delivered", "price_proposed", "disputed", "blocked"];

  return (
    <div className="detail">
      <div className="card">
        <div className="detail-head">
          <div>
            <div className="muted small">Заявка №{o.id} · {o.customer_name} → {o.carrier_name}</div>
            <h2>{o.route_from} → {o.route_to}</h2>
            <div className="muted">{o.cargo}</div>
          </div>
          <span className={"status big s-" + o.status}>{o.status_title}</span>
        </div>

        <div className="money">
          <Cell t="Цена в заявке" v={rub(o.price)} />
          <Cell t="Резерв (цена + 10%)" v={rub(o.reserve_total)} />
          <Cell t="Сейчас в резерве" v={rub(o.reserve_balance)} />
          {o.proposed_price != null && <Cell t="Предложено перевозчиком" v={rub(o.proposed_price)} />}
          {o.agreed_price != null && <Cell t="Согласовано" v={rub(o.agreed_price)} />}
          {o.downtime_comp > 0 && <Cell t="Компенсация простоя" v={"+" + rub(o.downtime_comp)} accent />}
        </div>

        {me.role === "carrier" && o.reserve_balance > 0 && <div className="guarantee">Оплата гарантирована: <b>{rub(o.reserve_balance)}</b> в резерве банка</div>}
        {o.is_duplicate && <div className="alert block">Флаг «дубль»: номер накладной уже финансируется по другой заявке. Платёж заблокирован.</div>}
        {o.status === "blocked" && (
          <div className="alert block">
            <b>Платёж заблокирован</b>
            <ul>{blocks.map((f, i) => <li key={i}>{f.message}</li>)}</ul>
            {me.role !== "customer" && <div className="small">Подайте исправленный документ — сверка пройдёт заново автоматически.</div>}
          </div>
        )}
        {err && <div className="alert block">{err}</div>}
        <Actions o={o} me={me} busy={busy} run={run} />
      </div>

      {me.role === "carrier" && simStatuses.includes(o.status) && <DocumentUpload order={o} run={run} busy={busy} />}

      <div className="grid2">
        <DocCard title="Накладная (ТТН)" doc={o.documents.waybill} findings={o.findings} type="waybill" scan={lastScan(o.scans, "waybill")} />
        <DocCard title="Путевой лист" doc={o.documents.trip_sheet} findings={o.findings} type="trip_sheet" scan={lastScan(o.scans, "trip_sheet")} />
      </div>

      {o.findings.length > 0 && (
        <div className="card">
          <h3>Сверка документов и трека</h3>
          <ul className="findings">
            {o.findings.map((f, i) => (
              <li key={i} className={"f-" + f.severity}>
                <span className="sev">{SEV_ICON[f.severity]}</span>
                <span className="msg">{f.message}</span>
                {f.guilty && f.severity !== "ok" && <span className="tag">виновник: {GUILTY[f.guilty]}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}

      {me.demo_mode && me.role !== "customer" && simStatuses.includes(o.status) && (
        <Simulator order={o} run={run} busy={busy} />
      )}

      {o.events.length > 0 && (
        <div className="card">
          <h3>Трек рейса</h3>
          <ol className="timeline">
            {o.events.map((e, i) => <li key={i}><span>{new Date(e.occurred_at).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" })}</span>{e.title}</li>)}
          </ol>
        </div>
      )}

      <div className="card">
        <h3>Лента решений по заявке</h3>
        <DecisionList items={o.decisions} />
      </div>
    </div>
  );
}

function Cell({ t, v, accent }: { t: string; v: string; accent?: boolean }) {
  return <div className={"cell" + (accent ? " accent" : "")}><span>{t}</span><b>{v}</b></div>;
}

function Actions({ o, me, busy, run }: { o: OrderDetail; me: Me; busy: boolean; run: (fn: () => Promise<unknown>) => void }) {
  const [price, setPrice] = useState((o.price / 100).toString());
  const [comment, setComment] = useState("");
  const [resolve, setResolve] = useState(((o.proposed_price ?? o.price) / 100).toString());
  const p = `/api/orders/${o.id}`;

  if (me.role === "customer" && o.status === "created")
    return <div className="actions"><button className="primary" disabled={busy} onClick={() => run(() => api.act(`${p}/reserve`))}>Зарезервировать {rub(o.reserve_total)}</button></div>;

  if (me.role === "carrier" && o.status === "reserved")
    return <div className="actions"><button className="primary" disabled={busy} onClick={() => run(() => api.act(`${p}/accept`))}>Принять заявку</button></div>;

  if (me.role === "carrier" && o.status === "delivered") {
    const amount = parseRub(price);
    return (
      <div className="actions">
        <label className="inline">Стоимость рейса, ₽<input value={price} onChange={(e) => setPrice(e.target.value)} /></label>
        <button className="primary" disabled={busy || !amount} onClick={() => run(() => api.act(`${p}/propose-price`, { amount }))}>Предложить стоимость</button>
      </div>
    );
  }

  if (me.role === "customer" && o.status === "price_proposed") {
    const total = (o.proposed_price ?? 0) + o.downtime_comp;
    return (
      <div className="actions column">
        <div>Перевозчик предлагает <b>{rub(o.proposed_price)}</b>{o.downtime_comp > 0 && <> + компенсация простоя <b>{rub(o.downtime_comp)}</b> = <b>{rub(total)}</b></>}</div>
        <div className="actions">
          <button className="primary" disabled={busy} onClick={() => run(() => api.act(`${p}/confirm-price`))}>Подтвердить и выплатить</button>
          <input placeholder="Причина спора" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="danger" disabled={busy || !comment.trim()} onClick={() => run(() => api.act(`${p}/dispute`, { comment }))}>Оспорить</button>
        </div>
      </div>
    );
  }

  if (me.role === "operator" && o.status === "disputed") {
    const amount = parseRub(resolve);
    return (
      <div className="actions column">
        <div>Спор: заказчик — <b>{rub(o.price)}</b>, перевозчик — <b>{rub(o.proposed_price)}</b></div>
        <div className="actions">
          <label className="inline">Решение, ₽<input value={resolve} onChange={(e) => setResolve(e.target.value)} /></label>
          <input placeholder="Комментарий оператора" value={comment} onChange={(e) => setComment(e.target.value)} />
          <button className="primary" disabled={busy || !amount || !comment.trim()} onClick={() => run(() => api.act(`${p}/resolve`, { amount, comment }))}>Решить и выплатить</button>
        </div>
      </div>
    );
  }
  return null;
}

const FIELDS: Record<string, [string, string][]> = {
  waybill: [["number", "Номер"], ["trip_sheet_number", "К путевому листу №"], ["driver_name", "Водитель"], ["driver_license", "Удостоверение"], ["vehicle_model", "Автомобиль"], ["vehicle_plate", "Госномер"]],
  trip_sheet: [["number", "Номер"], ["driver_name", "Водитель"], ["driver_license", "Удостоверение"], ["vehicle_model", "Автомобиль"], ["vehicle_plate", "Госномер"], ["vehicle_type", "Тип ТС"], ["medical_check", "Медосмотр"]],
};

function fmt(k: string, v: unknown) {
  if (k === "vehicle_type") return v === "car" ? "легковой" : "грузовой";
  if (k === "medical_check") return v ? "пройден" : "нет отметки";
  return v == null || v === "" ? "—" : String(v);
}

function lastScan(scans: ScanInfo[], type: string) {
  const s = scans.filter((x) => x.doc_type === type);
  return s.length ? s[s.length - 1] : undefined;
}

function DocCard({ title, doc, findings, type, scan }: { title: string; doc: Record<string, any> | null; findings: Finding[]; type: string; scan?: ScanInfo }) {
  // Поле подсвечивается, если на нём сработало блокирующее правило: «вот здесь ошибка».
  const bad = new Map(findings.filter((f) => f.severity === "block" && f.document === type && f.field).map((f) => [f.field!, f]));
  return (
    <div className={"card doc" + (bad.size ? " has-error" : "")}>
      <h3>{title}{doc?._version > 1 && <span className="tag">версия {doc!._version}</span>}{scan && <span className="tag">из скана</span>}{scan && <OriginalButton scan={scan} />}</h3>
      {!doc ? <div className="muted">Документ ещё не поступил</div> : (
        <dl>{FIELDS[type].map(([k, t]) => (
          <div key={k} className={bad.has(k) ? "bad" : ""} title={bad.get(k)?.message}>
            <dt>{t}</dt><dd>{fmt(k, doc[k])}</dd>
          </div>
        ))}</dl>
      )}
    </div>
  );
}
