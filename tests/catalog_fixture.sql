-- 仅在 tests/with_postgres.py 创建的一次性集群中执行。
CREATE SCHEMA catalog_demo;
GRANT USAGE ON SCHEMA catalog_demo TO test_reader;
CREATE TABLE catalog_demo."Wallet_Ledger" (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    currency text NOT NULL,
    status text DEFAULT 'open',
    UNIQUE (currency, status)
);
COMMENT ON TABLE catalog_demo."Wallet_Ledger" IS '结算记录，按 5%_ 计费';
COMMENT ON COLUMN catalog_demo."Wallet_Ledger".currency IS '币种';
CREATE UNIQUE INDEX open_currency ON catalog_demo."Wallet_Ledger"(currency) WHERE status = 'open';
GRANT SELECT ON catalog_demo."Wallet_Ledger" TO test_reader;
CREATE TABLE catalog_demo.hidden_wallet (id int);
CREATE TABLE catalog_demo.partial_access (visible int, private_key int UNIQUE);
GRANT SELECT (visible) ON catalog_demo.partial_access TO test_reader;
