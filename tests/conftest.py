"""
Ajustes compartilhados pela suíte de testes do Sistema de Orçamentos Públicos.

O backend é um pacote "achatado": `main.py` importa `database.*`, `routes.*` e
`services.*` como se `backend/` fosse a raiz do projeto (é assim que ele roda
em produção, com `cd backend && uvicorn main:app`). Por isso os testes
adicionam `backend/` ao sys.path antes de importar a aplicação.
"""
import os
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Durante os testes, os bancos SQLite vão para uma pasta temporária, nunca para
# o repositório nem para o volume de produção.
os.environ.setdefault("DATA_DIR", str(Path(__file__).resolve().parent / "_data"))
