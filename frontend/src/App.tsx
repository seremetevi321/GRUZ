import { useCallback, useEffect, useState } from "react";
import { api, ApiError, Me, Order, parseRub, rub } from "./api";
import { OrderView } from "./OrderView";
import { OperatorPanel } from "./Operator";
import { Feed } from "./Feed";

const DEMO = [
  { login: "alfa", title: "Альфа-Ритейл", role: "Заказчик" },
  { login: "beta", title: "Бета-Продукты", role: "Заказчик" },
  { login: "transline", title: "ТрансЛайн", role: "Перевозчик" },
  { login: "severtrans", title: "СеверТранс", role: "Перевозчик" },
  { login: "operator", title: "Банк", role: "Оператор" },
];

const ROLE_TITLE = { customer: "Заказчик", carrier: "Перевозчик", operator: "Оператор банка" } as const;

export function usePoll(fn: () => void, ms = 2000) {
  useEffect(() => {
    fn();
    const t = setInterval(fn, ms);
    return () => clearInterval(t);
  }, [fn, ms]);
}

export function App() {
  const [me, setMe] = useState<Me | null>(null);
  const [expired, setExpired] = useState(false);
  const refreshMe = useCallback(async () => {
    try { setMe(await api.get<Me>("/api/me")); }
    catch (e) { if (e instanceof ApiError && e.status === 401) { setMe(null); setExpired(true); } }
  }, []);
  if (!me) return <Login onDone={(m) => { setExpired(false); setMe(m); }} expired={expired} />;
  return <Shell me={me} refreshMe={refreshMe} onLogout={async () => { await api.logout(); setMe(null); }} />;
}

function Login({ onDone, expired }: { onDone: (m: Me) => void; expired: boolean }) {
  const preset = new URLSearchParams(location.search).get("as") ?? "";
  const [login, setLogin] = useState(preset);
  const [password, setPassword] = useState("");
  const [err, setErr] = useState(expired ? "Сессия истекла, войдите снова" : "");
  const [busy, setBusy] = useState(false);
  const submit = async (l = login, p = password) => {
    setBusy(true); setErr("");
    try { await api.login(l, p); onDone(await api.get<Me>("/api/me")); }
    catch (e) { setErr((e as Error).message); }
    finally { setBusy(false); }
  };
  return (
    <div className="login">
      <form className="card login-card" onSubmit={(e) => { e.preventDefault(); submit(); }}>
        <div className="brand"><span className="brand-mark">◆</span> Безопасная сделка</div>
        <p className="muted">Грузоперевозки с гарантией оплаты через резерв банка</p>
        <label>Логин<input value={login} onChange={(e) => setLogin(e.target.value)} autoFocus={!preset} autoComplete="username" /></label>
        <label>Пароль<input type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoFocus={!!preset} autoComplete="current-password" /></label>
        {err && <div className="alert block">{err}</div>}
        <button className="primary" disabled={busy || !login || !password}>Войти</button>
        <div className="demo-accounts">
          <div className="muted small">Демо-доступы (пароль из README):</div>
          <div className="chips">
            {DEMO.map((d) => (
              <button type="button" key={d.login} className={"chip" + (login === d.login ? " on" : "")} onClick={() => setLogin(d.login)}>
                {d.title}<span>{d.role}</span>
              </button>
            ))}
          </div>
        </div>
      </form>
    </div>
  );
}

function Shell({ me, refreshMe, onLogout }: { me: Me; refreshMe: () => Promise<void>; onLogout: () => void }) {
  const [orders, setOrders] = useState<Order[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [tab, setTab] = useState<"orders" | "new" | "panel" | "feed">(me.role === "operator" ? "panel" : "orders");
  const reload = useCallback(async () => {
    await refreshMe();
    try { setOrders(await api.get<Order[]>("/api/orders")); } catch { /* сеть — повторим в следующем цикле */ }
  }, [refreshMe]);
  usePoll(reload);

  const tabs: [typeof tab, string][] = [
    ...(me.role === "operator" ? [["panel", "Панель"] as [typeof tab, string]] : []),
    ["orders", "Заявки"],
    ...(me.role === "customer" ? [["new", "Новая заявка"] as [typeof tab, string]] : []),
    ["feed", "Лента решений"],
  ];

  return (
    <div className={"shell role-" + me.role}>
      <header className="top">
        <div className="brand"><span className="brand-mark">◆</span> Безопасная сделка</div>
        <nav>{tabs.map(([k, t]) => <button key={k} className={tab === k ? "on" : ""} onClick={() => { setTab(k); setSelected(null); }}>{t}</button>)}</nav>
        <div className="who">
          {me.role !== "operator" && (
            <div className="balances">
              <div><span>Доступно</span><b>{rub(me.balances.available)}</b></div>
              {me.role === "customer" && <div><span>В резерве</span><b>{rub(me.balances.reserved)}</b></div>}
              {me.balances.invoice !== 0 && <div><span>{me.balances.invoice < 0 ? "К оплате по счетам" : "Счета к получению"}</span><b>{rub(Math.abs(me.balances.invoice))}</b></div>}
            </div>
          )}
          <div className="user"><b>{me.company_name}</b><span>{ROLE_TITLE[me.role]} · {me.full_name}</span></div>
          <button className="ghost" onClick={onLogout}>Выйти</button>
        </div>
      </header>
      {me.demo_mode && <div className="demo-banner">Демо: ЭДО, путевые листы, трек и счета банка имитируются. В пилоте — адаптеры к внешним системам и АБС.</div>}
      <main>
        {tab === "panel" && <OperatorPanel me={me} orders={orders} onOpen={(id) => { setSelected(id); setTab("orders"); }} reload={reload} />}
        {tab === "new" && <NewOrder onCreated={(id) => { setSelected(id); setTab("orders"); reload(); }} me={me} />}
        {tab === "feed" && <Feed />}
        {tab === "orders" && (
          <div className="split">
            <OrderList orders={orders} selected={selected} onSelect={setSelected} me={me} />
            {selected ? <OrderView key={selected} id={selected} me={me} onChanged={reload} />
              : <div className="card empty">Выберите заявку слева{me.role === "customer" ? " или создайте новую" : ""}</div>}
          </div>
        )}
      </main>
    </div>
  );
}

function OrderList({ orders, selected, onSelect, me }: { orders: Order[]; selected: number | null; onSelect: (id: number) => void; me: Me }) {
  if (!orders.length) return <div className="card list empty">Заявок пока нет</div>;
  return (
    <div className="card list">
      {orders.map((o) => (
        <button key={o.id} className={"row" + (o.id === selected ? " on" : "")} onClick={() => onSelect(o.id)}>
          <div className="row-top"><b>№{o.id}</b> {o.route_from} → {o.route_to}</div>
          <div className="row-bottom">
            <span className={"status s-" + o.status}>{o.status_title}</span>
            <span>{rub(o.price)}</span>
          </div>
          {me.role === "operator" && <div className="muted small">{o.customer_name} → {o.carrier_name}</div>}
          {o.is_duplicate && <div className="flag">дубль</div>}
        </button>
      ))}
    </div>
  );
}

function NewOrder({ onCreated, me }: { onCreated: (id: number) => void; me: Me }) {
  const [carriers, setCarriers] = useState<{ id: number; name: string }[]>([]);
  const [f, setF] = useState({ carrier_id: 0, route_from: "Москва, ул. Ленина, 40", route_to: "Москва, ул. Советская, 69", cargo: "Продукты, 20 мест", price: "120 000" });
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  useEffect(() => { api.get<{ id: number; name: string }[]>("/api/carriers").then((c) => { setCarriers(c); setF((x) => ({ ...x, carrier_id: c[0]?.id ?? 0 })); }); }, []);
  const price = parseRub(f.price);
  const total = price ? price + Math.floor(price / 10) : null;
  const submit = async () => {
    if (!price) return setErr("Укажите цену в рублях");
    setBusy(true); setErr("");
    try {
      const r = await api.act<{ id: number }>("/api/orders", { ...f, price });
      onCreated(r.id);
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  };
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [k]: k === "carrier_id" ? Number(e.target.value) : e.target.value });
  return (
    <div className="card form narrow">
      <h2>Новая заявка</h2>
      <label>Перевозчик<select value={f.carrier_id} onChange={set("carrier_id")}>{carriers.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
      <label>Откуда<input value={f.route_from} onChange={set("route_from")} /></label>
      <label>Куда<input value={f.route_to} onChange={set("route_to")} /></label>
      <label>Груз<input value={f.cargo} onChange={set("cargo")} /></label>
      <label>Цена рейса, ₽<input value={f.price} onChange={set("price")} inputMode="decimal" /></label>
      <div className="reserve-preview">
        <span>К резервированию</span>
        <b>{total ? rub(total) : "—"}</b>
        <small>цена + 10% на возможный простой и неустойки</small>
      </div>
      {total && total > me.balances.available && <div className="alert warning">На счёте доступно {rub(me.balances.available)} — резерв не пройдёт</div>}
      {err && <div className="alert block">{err}</div>}
      <button className="primary" disabled={busy} onClick={submit}>Создать заявку</button>
    </div>
  );
}
