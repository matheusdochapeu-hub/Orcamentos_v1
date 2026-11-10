"""
Testes de fumaça da API do Sistema de Orçamentos Públicos.

Não batem em serviços externos (Receita, PNCP, Google, OSM): validam apenas a
aplicação em si — rotas, cabeçalhos de PWA, tratamento de erro e o fallback
do SPA. É a barreira mínima que impede um commit quebrado de ir ao ar.
"""
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from main import app

    with TestClient(app) as c:
        yield c


def test_health_responde_ok(client):
    resposta = client.get("/api/health")
    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["status"] == "ok"
    assert "versao" in corpo


def test_raiz_serve_o_frontend(client):
    resposta = client.get("/")
    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]
    assert "Orçamentos" in resposta.text or "orcamentos" in resposta.text.lower()


def test_service_worker_tem_escopo_na_raiz(client):
    resposta = client.get("/sw.js")
    assert resposta.status_code == 200
    assert resposta.headers.get("service-worker-allowed") == "/"
    assert "no-cache" in resposta.headers.get("cache-control", "")


def test_manifest_pwa_e_servido(client):
    resposta = client.get("/manifest.webmanifest")
    assert resposta.status_code == 200
    dados = resposta.json()
    assert "name" in dados
    assert "icons" in dados and len(dados["icons"]) > 0


def test_rota_de_api_inexistente_retorna_404(client):
    resposta = client.get("/api/rota-que-nao-existe")
    assert resposta.status_code == 404


def test_rota_desconhecida_cai_no_fallback_do_spa(client):
    resposta = client.get("/alguma/tela/interna")
    assert resposta.status_code == 200
    assert "text/html" in resposta.headers["content-type"]


def test_headers_de_embed_presentes(client):
    # O app é embutido como ferramenta interna do Portal Gestor Público;
    # sem frame-ancestors o navegador bloqueia o iframe.
    resposta = client.get("/")
    assert "frame-ancestors" in resposta.headers.get("content-security-policy", "")


def test_status_do_cache_catmat_responde(client):
    # Não baixa o catálogo do governo (isso levaria minutos); apenas garante que
    # a rota de diagnóstico responde e devolve o formato esperado.
    resposta = client.get("/api/catmat/status-cache")
    assert resposta.status_code == 200
    assert isinstance(resposta.json(), (dict, list))
