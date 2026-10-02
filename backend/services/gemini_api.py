"""
Integração opcional com Gemini API para melhorar o ranqueamento de CATMAT/CATSER.
O usuário fornece sua própria chave via configuração.
"""
import httpx
import json
import os

GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
TIMEOUT = 30.0


async def ranquear_com_gemini(
    query: str,
    descricao: str,
    tipo: str,
    candidatos: list[dict],
    api_key: str,
) -> list[dict]:
    """
    Usa Gemini para reordenar e melhorar o score dos candidatos CATMAT/CATSER.

    Args:
        query: nome do item buscado
        descricao: descrição adicional
        tipo: 'CATMAT' | 'CATSER'
        candidatos: lista de candidatos já buscados na API do governo
        api_key: chave Gemini do usuário

    Returns:
        Lista reordenada com campo 'score_gemini' adicionado
    """
    if not candidatos or not api_key:
        return candidatos

    # Prepara prompt
    lista_candidatos = "\n".join(
        f"{i+1}. [{c['codigo']}] {c['descricao']}"
        for i, c in enumerate(candidatos)
    )

    prompt = f"""Você é um especialista em compras públicas brasileiras.

O usuário precisa encontrar o código {"CATMAT" if tipo == "CATMAT" else "CATSER"} correto para:
- Nome: {query}
- Descrição adicional: {descricao or "Não informada"}

Abaixo estão os candidatos encontrados na base do governo:
{lista_candidatos}

Analise cada candidato e retorne um JSON com a seguinte estrutura:
{{
  "ranqueamento": [
    {{"numero": 1, "score": 0.95, "justificativa": "..."}}
  ]
}}

Onde:
- "numero" é o número do candidato na lista acima (1 a {len(candidatos)})
- "score" é um valor de 0.0 a 1.0 indicando a probabilidade de ser o item correto
- "justificativa" é uma breve explicação (máximo 100 caracteres)

Retorne APENAS o JSON, sem texto adicional."""

    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.post(
                f"{GEMINI_URL}?key={api_key}",
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.1,
                        "maxOutputTokens": 1024,
                    },
                },
            )
            data = r.json()

        # Extrai o texto da resposta
        texto = (
            data.get("candidates", [{}])[0]
            .get("content", {})
            .get("parts", [{}])[0]
            .get("text", "")
        )

        # Parse do JSON retornado
        # Remove possíveis blocos markdown
        texto_clean = texto.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        resultado = json.loads(texto_clean)
        ranqueamento = resultado.get("ranqueamento", [])

        # Aplica os scores do Gemini aos candidatos
        mapa = {str(item["numero"]): item for item in ranqueamento}
        for i, candidato in enumerate(candidatos):
            info = mapa.get(str(i + 1), {})
            candidato["score"] = info.get("score", candidato.get("score", 0.0))
            candidato["justificativa_ia"] = info.get("justificativa", "")
            candidato["fonte"] = candidato.get("fonte", "") + " + Gemini"

        candidatos.sort(key=lambda x: x["score"], reverse=True)

    except Exception as e:
        print(f"[Gemini] Erro ao ranquear: {e}")
        # Falha silenciosa — mantém ordenação original

    return candidatos
