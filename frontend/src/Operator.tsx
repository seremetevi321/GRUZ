import { useCallback, useState } from "react";
import { api, Invariants, Me, Metrics, Order, rub } from "./api";
import { usePoll } from "./App";
import { Feed } from "./Feed";

const pct = (x: number | null) => (x == null ? "—" : `${Math.round(x * 100)}%`);
const secs = (s: number | null) => (s == null ? "—" : s < 1 ? "< 1 с" : `${s.toFixed(1)} с`);

export function OperatorPanel({ orders, onOpen, reload }: { me: Me; orders: Order[]; onOpen: (id: number) => void; reload: () => void }) {
  const [m, setM] = useState<Metrics | null>(null);
  const [inv, setInv] = useState<Invariants | null>(null);
  const [busy, setBusy] = useState(false);
  const load = useCallback(async () => {
    try { setM(await api.get<Metrics>("/api/operator/metrics")); setInv(await api.get<Invariants>("/api/operator/invariants")); } catch { /* повторим */ }
  }, []);
  usePoll(load);
  const attention = orders.filter((o) => ["disputed", "blocked"].includes(o.status) || o.is_duplicate);
  const [confirming, setConfirming] = useState(false);
  const reset = async () => {
    if (!confirming) { setConfirming(true); setTimeout(() => setConfirming(false), 4000); return; }
    setBusy(true); setConfirming(false);
    try { await api.act("/api/admin/reset"); } finally { setBusy(false); load(); reload(); }
  };
  return (
    <div className="operator">
      <div className="kpis">
        <Kpi t="От согласования до выплаты" v={secs(m?.avg_agree_to_pay_seconds ?? null)} />
        <Kpi t="Подтверждённые рейсы" v={pct(m?.confirmed_share ?? null)} sub={m ? `${m.confirmed} из ${m.reached_delivery}` : ""} />
        <Kpi t="Заблокированные платежи" v={m ? String(m.blocked_count) : "—"} sub={m ? rub(m.blocked_sum) : ""} />
        <Kpi t="Доля споров" v={pct(m?.dispute_share ?? null)} sub={m ? `споров: ${m.disputes}` : ""} />
        <Kpi t="Компенсации простоя" v={m ? rub(m.downtime_comp_sum) : "—"} />
        <Kpi t="Найдено дублей" v={m ? String(m.duplicates) : "—"} />
      </div>
      <div className="grid2">
        <div className="card">
          <h3>Требуют внимания</h3>
          {!attention.length ? <div className="muted">Нет споров и блокировок</div> : (
            <ul className="attention">{attention.map((o) => (
              <li key={o.id}><button className="link" onClick={() => onOpen(o.id)}>Заявка №{o.id}</button> <span className={"status s-" + o.status}>{o.is_duplicate ? "дубль" : o.status_title}</span> {o.customer_name} → {o.carrier_name}</li>
            ))}</ul>
          )}
        </div>
        <div className="card">
          <h3>Контроль денег</h3>
          {inv && (
            <div className={"invariant " + (inv.ok ? "good" : "bad")}>
              <b>{inv.ok ? "✓ Баланс сходится" : "✕ Нарушен баланс"}</b>
              <div className="small">Деньги клиентов в системе: {rub(inv.client_money)} · внесено: {rub(inv.funded)}</div>
              <div className="small">Несбалансированных проводок: {inv.unbalanced_txns} · расхождений счетов: {inv.accounts_out_of_sync}</div>
            </div>
          )}
          <button className="danger" disabled={busy} onClick={reset}>{confirming ? "Нажмите ещё раз для сброса" : "Сбросить данные"}</button>
        </div>
      </div>
      <Feed />
    </div>
  );
}

function Kpi({ t, v, sub }: { t: string; v: string; sub?: string }) {
  return <div className="card kpi"><span>{t}</span><b>{v}</b>{sub && <small>{sub}</small>}</div>;
}
