-- Безопасная сделка для грузоперевозок: базовая схема.
-- Все суммы — BIGINT в копейках. Никаких float.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ---------- Участники и доступ ----------

CREATE TABLE companies (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT NOT NULL UNIQUE,
    kind        TEXT NOT NULL CHECK (kind IN ('customer', 'carrier', 'bank'))
);

CREATE TABLE users (
    id            BIGSERIAL PRIMARY KEY,
    login         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    full_name     TEXT NOT NULL,
    role          TEXT NOT NULL CHECK (role IN ('customer', 'carrier', 'operator')),
    company_id    BIGINT NOT NULL REFERENCES companies(id)
);

-- В БД лежит только SHA-256 токена: утечка таблицы не даёт войти.
CREATE TABLE sessions (
    token_hash  BYTEA PRIMARY KEY,
    user_id     BIGINT NOT NULL REFERENCES users(id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at  TIMESTAMPTZ NOT NULL
);

-- ---------- Леджер (двойная запись) ----------

-- funding  — внешний источник (счёт банка-эмитента для сидов), может быть отрицательным;
-- available — доступные деньги компании, не может уйти в минус;
-- reserve   — резерв конкретной заявки, не может уйти в минус;
-- invoice   — взаиморасчёты сверх резерва (минус = компания должна, плюс = ей должны).
CREATE TABLE accounts (
    id          BIGSERIAL PRIMARY KEY,
    company_id  BIGINT REFERENCES companies(id),
    order_id    BIGINT,
    kind        TEXT NOT NULL CHECK (kind IN ('funding', 'available', 'reserve', 'invoice')),
    balance     BIGINT NOT NULL DEFAULT 0,
    CONSTRAINT non_negative CHECK (kind IN ('funding', 'invoice') OR balance >= 0)
);
CREATE UNIQUE INDEX accounts_company_kind ON accounts(company_id, kind) WHERE order_id IS NULL;
CREATE UNIQUE INDEX accounts_order_reserve ON accounts(order_id) WHERE kind = 'reserve';

CREATE TABLE ledger_txns (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT,
    kind        TEXT NOT NULL,
    memo        TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ledger_lines (
    id          BIGSERIAL PRIMARY KEY,
    txn_id      BIGINT NOT NULL REFERENCES ledger_txns(id),
    account_id  BIGINT NOT NULL REFERENCES accounts(id),
    amount      BIGINT NOT NULL CHECK (amount <> 0)
);
CREATE INDEX ledger_lines_txn ON ledger_lines(txn_id);

-- Баланс счёта обновляется самой базой при каждой проводке;
-- CHECK non_negative на accounts не даст уйти в минус.
CREATE FUNCTION ledger_apply_line() RETURNS trigger AS $$
BEGIN
    UPDATE accounts SET balance = balance + NEW.amount WHERE id = NEW.account_id;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_lines_apply AFTER INSERT ON ledger_lines
    FOR EACH ROW EXECUTE FUNCTION ledger_apply_line();

-- Проводка должна сходиться в ноль. Проверка отложена до COMMIT,
-- поэтому несбалансированная транзакция просто не сможет закоммититься.
CREATE FUNCTION ledger_check_balanced() RETURNS trigger AS $$
DECLARE s BIGINT;
BEGIN
    SELECT COALESCE(SUM(amount), 0) INTO s FROM ledger_lines WHERE txn_id = NEW.txn_id;
    IF s <> 0 THEN
        RAISE EXCEPTION 'unbalanced ledger txn % (sum=%)', NEW.txn_id, s;
    END IF;
    RETURN NULL;
END $$ LANGUAGE plpgsql;

CREATE CONSTRAINT TRIGGER ledger_lines_balanced AFTER INSERT ON ledger_lines
    DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION ledger_check_balanced();

-- Журналы только на добавление.
CREATE FUNCTION forbid_change() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'table % is append-only', TG_TABLE_NAME;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER ledger_txns_append_only BEFORE UPDATE OR DELETE ON ledger_txns
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER ledger_lines_append_only BEFORE UPDATE OR DELETE ON ledger_lines
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- Баланс меняется только через проводку.
CREATE FUNCTION accounts_guard_balance() RETURNS trigger AS $$
BEGIN
    IF NEW.balance <> OLD.balance AND pg_trigger_depth() < 2 THEN
        RAISE EXCEPTION 'account balance can only change through ledger_lines';
    END IF;
    RETURN NEW;
END $$ LANGUAGE plpgsql;

CREATE TRIGGER accounts_balance_guard BEFORE UPDATE ON accounts
    FOR EACH ROW EXECUTE FUNCTION accounts_guard_balance();

-- ---------- Заявки ----------

CREATE TABLE orders (
    id                  BIGSERIAL PRIMARY KEY,
    customer_id         BIGINT NOT NULL REFERENCES companies(id),
    carrier_id          BIGINT NOT NULL REFERENCES companies(id),
    route_from          TEXT NOT NULL,
    route_to            TEXT NOT NULL,
    cargo               TEXT NOT NULL,
    price               BIGINT NOT NULL CHECK (price > 0),
    buffer              BIGINT NOT NULL CHECK (buffer >= 0),
    loading_norm_min    INT NOT NULL DEFAULT 120,
    unloading_norm_min  INT NOT NULL DEFAULT 120,
    downtime_rate_hour  BIGINT NOT NULL DEFAULT 100000,  -- 1 000 ₽/ч в копейках
    status              TEXT NOT NULL CHECK (status IN (
                            'created', 'reserved', 'accepted', 'in_transit', 'delivered',
                            'price_proposed', 'disputed', 'blocked', 'paid',
                            'cancelled', 'rejected')),
    proposed_price      BIGINT,
    agreed_price        BIGINT,
    downtime_comp       BIGINT NOT NULL DEFAULT 0,
    is_duplicate        BOOLEAN NOT NULL DEFAULT false,
    price_agreed_at     TIMESTAMPTZ,
    paid_at             TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX orders_customer ON orders(customer_id);
CREATE INDEX orders_carrier ON orders(carrier_id);

ALTER TABLE accounts ADD CONSTRAINT accounts_order_fk FOREIGN KEY (order_id) REFERENCES orders(id);
ALTER TABLE ledger_txns ADD CONSTRAINT ledger_txns_order_fk FOREIGN KEY (order_id) REFERENCES orders(id);

-- ---------- Документы и события симулятора ----------

-- Каждая версия документа хранится; актуальна последняя по типу.
CREATE TABLE documents (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id),
    doc_type    TEXT NOT NULL CHECK (doc_type IN ('waybill', 'trip_sheet')),
    number      TEXT NOT NULL,
    version     INT NOT NULL,
    data        JSONB NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (order_id, doc_type, version)
);

-- Номер накладной закрепляется за одной заявкой навсегда: защита от повторного финансирования.
CREATE TABLE waybill_registry (
    number      TEXT PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id)
);

CREATE TABLE trip_events (
    id          BIGSERIAL PRIMARY KEY,
    event_id    TEXT NOT NULL UNIQUE,          -- ключ идемпотентности источника
    order_id    BIGINT NOT NULL REFERENCES orders(id),
    kind        TEXT NOT NULL,
    occurred_at TIMESTAMPTZ NOT NULL,
    payload     JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX trip_events_order ON trip_events(order_id);

-- ---------- Сверка и лента решений ----------

CREATE TABLE findings (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT NOT NULL REFERENCES orders(id),
    run_no      INT NOT NULL,
    rule        TEXT NOT NULL,
    severity    TEXT NOT NULL CHECK (severity IN ('ok', 'info', 'warning', 'block')),
    document    TEXT,
    field       TEXT,
    message     TEXT NOT NULL,
    guilty      TEXT CHECK (guilty IN ('customer', 'carrier')),
    amount      BIGINT,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX findings_order_run ON findings(order_id, run_no);

CREATE TABLE decisions (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT REFERENCES orders(id),
    actor       TEXT NOT NULL,                 -- 'system' или логин
    kind        TEXT NOT NULL,
    amount      BIGINT,
    message     TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX decisions_order ON decisions(order_id);

CREATE TRIGGER decisions_append_only BEFORE UPDATE OR DELETE ON decisions
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER findings_append_only BEFORE UPDATE OR DELETE ON findings
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

-- ---------- Идемпотентность действий пользователей ----------

CREATE TABLE idempotency_keys (
    user_id       BIGINT NOT NULL REFERENCES users(id),
    key           TEXT NOT NULL,
    request_hash  TEXT NOT NULL,
    status_code   INT NOT NULL,
    response      JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, key)
);
