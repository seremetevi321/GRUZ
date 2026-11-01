-- Модуль анализа документов: оригинал файла, что распознала система и что подтвердил человек.
CREATE TABLE document_scans (
    id             BIGSERIAL PRIMARY KEY,
    order_id       BIGINT NOT NULL REFERENCES orders(id),
    uploaded_by    BIGINT NOT NULL REFERENCES users(id),
    doc_type       TEXT NOT NULL CHECK (doc_type IN ('waybill', 'trip_sheet')),
    detected_type  TEXT,
    mime           TEXT NOT NULL,
    size_bytes     INT NOT NULL,
    sha256         TEXT NOT NULL,
    content        BYTEA NOT NULL,
    recognized     JSONB NOT NULL,
    confirmed      JSONB,
    corrections    JSONB,
    document_id    BIGINT REFERENCES documents(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmed_at   TIMESTAMPTZ
);
CREATE INDEX document_scans_order ON document_scans(order_id);
CREATE INDEX document_scans_sha ON document_scans(sha256);
