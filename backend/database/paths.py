"""
Resolução centralizada do diretório de dados (bancos SQLite).

Por padrão usa a pasta `backend/database/` (compatível com execução local e
Render). Em produção com disco persistente (ex.: volume do Fly.io), basta
definir a variável de ambiente DATA_DIR apontando para o volume montado
(ex.: DATA_DIR=/data) — assim os bancos SQLite sobrevivem a deploys/reinícios.

Uso:
    from database.paths import DATA_DIR, db_path
    CAMINHO = db_path("catmat_cache.db")
"""
from __future__ import annotations

import os
from pathlib import Path

# Diretório padrão: backend/database/ (este arquivo está em backend/database/)
_PADRAO = Path(__file__).resolve().parent

# DATA_DIR permite redirecionar os bancos para um volume persistente.
DATA_DIR = Path(os.getenv("DATA_DIR", str(_PADRAO)))

try:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    # Se não puder criar (ex.: permissão), cai para o padrão.
    DATA_DIR = _PADRAO
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def db_path(nome_arquivo: str) -> Path:
    """Retorna o caminho de um arquivo de banco dentro do diretório de dados."""
    return DATA_DIR / nome_arquivo
