"""
Modelos e inicialização do banco de dados SQLite (via aiosqlite).
"""
import aiosqlite
import json
from pathlib import Path

from database.paths import db_path

DB_PATH = db_path("orcamentos.db")


async def get_db():
    return await aiosqlite.connect(DB_PATH)


async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.executescript("""
            CREATE TABLE IF NOT EXISTS buscas (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                criado_em   TEXT    NOT NULL DEFAULT (datetime('now','localtime')),
                tipo        TEXT    NOT NULL,          -- CATMAT | CATSER
                catcodigo   TEXT    NOT NULL,
                catdescricao TEXT   NOT NULL,
                cidade      TEXT    NOT NULL,
                estado      TEXT,
                raio_km     INTEGER NOT NULL,
                tipo_busca  TEXT    NOT NULL,          -- exata | similaridade
                total_empresas INTEGER DEFAULT 0,
                nome_busca  TEXT
            );

            CREATE TABLE IF NOT EXISTS empresas_busca (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                busca_id    INTEGER NOT NULL REFERENCES buscas(id) ON DELETE CASCADE,
                place_id    TEXT,
                nome        TEXT    NOT NULL,
                endereco    TEXT,
                telefone    TEXT,
                whatsapp    TEXT,
                email       TEXT,
                site        TEXT,
                latitude    REAL,
                longitude   REAL,
                relevancia  INTEGER DEFAULT 0,
                dados_extra TEXT    -- JSON com dados adicionais
            );

            CREATE TABLE IF NOT EXISTS cache_places (
                cache_key   TEXT PRIMARY KEY,
                dados       TEXT NOT NULL,
                criado_em   TEXT NOT NULL DEFAULT (datetime('now','localtime')),
                expira_em   TEXT NOT NULL
            );
        """)
        await db.commit()
