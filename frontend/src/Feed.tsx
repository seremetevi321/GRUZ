import { useCallback, useState } from "react";
import { api, Decision } from "./api";
import { usePoll } from "./App";

const KIND_CLASS: Record<string, string> = {
  paid: "good", refund: "good", payout_blocked: "bad", duplicate: "bad", refused: "bad", disputed: "warn",
  dispute_resolved: "warn", invoiced: "warn",
};

export function DecisionList({ items }: { items: Decision[] }) {
  if (!items.length) return <div className="muted">Пока пусто</div>;
  return (
    <ul className="feed">
      {items.map((d) => (
        <li key={d.id} className={KIND_CLASS[d.kind] ?? ""}>
          <div className="feed-meta">
            {new Date(d.created_at).toLocaleTimeString("ru-RU")} · {d.actor === "system" ? "система" : d.actor === "simulator" ? "симулятор" : d.actor}
            {d.order_id && <> · заявка №{d.order_id}</>}
          </div>
          <div>{d.message}</div>
        </li>
      ))}
    </ul>
  );
}

export function Feed() {
  const [items, setItems] = useState<Decision[]>([]);
  const load = useCallback(async () => { try { setItems(await api.get<Decision[]>("/api/decisions")); } catch { /* повторим */ } }, []);
  usePoll(load);
  return <div className="card"><h2>Лента решений</h2><DecisionList items={items} /></div>;
}


