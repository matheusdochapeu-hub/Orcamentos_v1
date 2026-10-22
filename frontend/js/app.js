/* ═══════════════════════════════════════════════════════════
   Sistema de Orçamentos Públicos — JavaScript Principal
   ═══════════════════════════════════════════════════════════ */

// ── Estado global da aplicação ───────────────────────────────
const ESTADO = {
  etapaAtual: 1,
  tipo: null,            // 'material' | 'servico'
  modoBusca: 'nome',     // 'nome' | 'descricao'
  itemSelecionado: null, // { codigo, descricao, tipo }
  tipoBusca: 'exata',
  fonte: 'ambas',        // busca em todas as fontes possíveis (não exposto na tela)
  raioKm: 10,
  empresas: [],
  buscaId: null,
};

// Determina a URL base da API do backend.
// 1. Prioridade para configuração salva no navegador (localStorage: backend_url).
// 2. Se rodando no GitHub Pages (*.github.io ou *.github.dev), conecta ao backend remoto (Fly.io).
// 3. Em ambiente local ou servidor integrado, usa a própria origem (window.location.origin).
function obterApiUrl() {
  const custom = localStorage.getItem('backend_url');
  if (custom && custom.trim()) {
    return custom.trim().replace(/\/+$/, '');
  }
  const host = window.location.hostname;
  if (host.endsWith('github.io') || host.endsWith('github.dev')) {
    return 'https://orcamentos-publicos.fly.dev';
  }
  return window.location.origin;
}

let API = obterApiUrl();

// Faz fetch + parse de JSON de forma DEFENSIVA. Se a resposta vier vazia (ex.:
// o servidor/proxy cortou um request longo e devolveu corpo vazio), em vez de
// quebrar com "Unexpected end of JSON input" lançamos uma mensagem clara.
async function fetchJson(url, opcoes) {
  const resp = await fetch(url, opcoes);
  const texto = await resp.text();
  let data = null;
  if (texto) {
    try { data = JSON.parse(texto); }
    catch (_) { /* corpo não-JSON */ }
  }
  if (!resp.ok) {
    const msg = (data && (data.detail || data.mensagem)) ||
      (resp.status === 502 || resp.status === 504
        ? 'A consulta demorou demais e foi interrompida. Tente um termo mais específico ou tente de novo em instantes.'
        : `Erro ${resp.status} ao consultar o servidor.`);
    const err = new Error(msg); err.status = resp.status; throw err;
  }
  if (data === null) {
    throw new Error('A consulta demorou demais e não retornou dados. Tente novamente ou use um termo mais específico.');
  }
  return data;
}

// ── Inicialização ────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  // Inicializa estado no History API se não existir
  if (!history.state || !history.state.etapa) {
    history.replaceState({ etapa: 1 }, '', '#etapa-1');
  }
  // Marca o raio padrão como selecionado
  selecionarRaio(10);
  registrarPWA();
  aplicarModoEmbed();
  configurarBotaoInstalar();
  // SP já vem selecionado → carrega os municípios de SP de cara
  aoMudarEstado();
  // Revalida o formulário de empresas conforme o usuário digita a cidade
  const cidadeInput = document.getElementById('cidade-input');
  if (cidadeInput) cidadeInput.addEventListener('input', validarFormEmpresas);
  validarFormEmpresas();
});

// Modo "ferramenta interna": quando embutido em iframe (?embed=1 ou dentro de
// outro site), esconde o cabeçalho grande para integrar melhor ao portal.
function aplicarModoEmbed() {
  const params = new URLSearchParams(window.location.search);
  const emIframe = window.self !== window.top;
  const pedidoEmbed = params.get('embed') === '1' || params.get('embed') === 'true';
  if (pedidoEmbed || emIframe) {
    document.body.classList.add('embed');
  }
}

// ── Municípios por estado (autocomplete da cidade) ───────────
const _CACHE_MUN = {};  // uf -> [nomes]

async function aoMudarEstado() {
  const uf = (document.getElementById('estado-input')?.value || '').trim();
  const datalist = document.getElementById('lista-municipios');
  const hint = document.getElementById('cidade-hint');
  const cidadeInput = document.getElementById('cidade-input');
  if (!datalist) return;

  datalist.innerHTML = '';
  if (cidadeInput) cidadeInput.value = '';
  if (!uf) { if (hint) hint.textContent = 'Escolha o estado para listar as cidades.'; return; }

  if (hint) hint.textContent = 'Carregando cidades...';
  try {
    let nomes = _CACHE_MUN[uf];
    if (!nomes) {
      const resp = await fetch(`${API}/api/localidades/municipios/${uf}`);
      const data = await resp.json();
      nomes = data.municipios || [];
      _CACHE_MUN[uf] = nomes;
    }
    datalist.innerHTML = nomes.map(n => `<option value="${escapeHtml(n)}"></option>`).join('');
    if (hint) hint.textContent = nomes.length
      ? `${nomes.length} cidades em ${uf} — digite para filtrar.`
      : 'Não foi possível carregar a lista; você pode digitar o nome mesmo assim.';
  } catch (e) {
    if (hint) hint.textContent = 'Não foi possível carregar a lista; digite o nome da cidade manualmente.';
  }
  validarFormEmpresas();
}

// Valida o formulário de busca de empresas ANTES de permitir buscar.
// Desabilita o botão e explica o que falta, para o usuário não disparar uma
// busca longa só para descobrir no fim que faltou preencher um campo.
function validarFormEmpresas() {
  const estado = (document.getElementById('estado-input')?.value || '').trim();
  const cidade = (document.getElementById('cidade-input')?.value || '').trim();
  const btn = document.getElementById('btn-buscar-emp');
  const aviso = document.getElementById('form-emp-aviso');
  if (!btn) return true;

  const faltando = [];
  if (!estado) faltando.push('estado');
  if (!cidade) faltando.push('cidade');

  const ok = faltando.length === 0;
  btn.disabled = !ok;   // visual via .btn:disabled no CSS
  if (aviso) {
    if (ok) {
      aviso.textContent = '';
      aviso.classList.add('hidden');
    } else {
      const faltaTxt = faltando.length === 2
        ? 'o estado e a cidade'
        : (faltando[0] === 'estado' ? 'o estado' : 'a cidade');
      aviso.textContent = `⚠️ Selecione ${faltaTxt} para buscar.`;
      aviso.classList.remove('hidden');
    }
  }
  return ok;
}

// Registra o Service Worker (PWA — permite instalar como app e abrir rápido)
function registrarPWA() {
  if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
      navigator.serviceWorker.register('./sw.js').catch(err =>
        console.warn('SW não registrado:', err)
      );
    });
  }
}

// ═══════════════════ BOTÃO "BAIXAR APP" (PWA) ════════════════
// Permite instalar o app no computador ou celular (vira ícone na tela inicial
// e abre em tela cheia, funcionando como um app nativo — tudo gratuito).
let _promptInstalar = null;

// Captura o evento JÁ no carregamento do script (ele pode disparar ANTES do
// DOMContentLoaded). Guarda o prompt e tenta revelar o botão se ele já existir.
window.addEventListener('beforeinstallprompt', (e) => {
  e.preventDefault();
  _promptInstalar = e;
  const btn = document.getElementById('btn-instalar');
  if (btn && !document.body.classList.contains('embed')) btn.classList.remove('hidden');
});
window.addEventListener('appinstalled', () => {
  _promptInstalar = null;
  document.getElementById('btn-instalar')?.classList.add('hidden');
  if (typeof toast === 'function') toast('App instalado! Procure o ícone na sua tela inicial.', 'info');
});

function configurarBotaoInstalar() {
  const btn = document.getElementById('btn-instalar');
  if (!btn) return;

  // Já instalado / aberto como app: não mostra o botão.
  const jaInstalado = window.matchMedia('(display-mode: standalone)').matches
    || window.navigator.standalone === true;
  if (jaInstalado) { btn.classList.add('hidden'); return; }

  // No modo embed (dentro de outro site) não faz sentido instalar.
  if (document.body.classList.contains('embed')) { btn.classList.add('hidden'); return; }

  // Se o evento já foi capturado (Android/desktop), revela o botão agora.
  if (_promptInstalar) { btn.classList.remove('hidden'); return; }

  // iPhone/iPad (Safari não dispara beforeinstallprompt): mostramos o botão
  // mesmo assim e, ao clicar, explicamos como adicionar à tela inicial.
  const ehIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (ehIOS) btn.classList.remove('hidden');
}

async function instalarApp() {
  // Caminho padrão (Android/desktop): usa o prompt nativo do navegador.
  if (_promptInstalar) {
    _promptInstalar.prompt();
    try { await _promptInstalar.userChoice; } catch (_) {}
    _promptInstalar = null;
    document.getElementById('btn-instalar')?.classList.add('hidden');
    return;
  }

  // iOS ou navegador sem suporte ao prompt: instruções passo a passo.
  const ehIOS = /iphone|ipad|ipod/i.test(navigator.userAgent);
  if (ehIOS) {
    alert(
      'Para instalar no iPhone/iPad:\n\n' +
      '1) Toque no botão Compartilhar (quadrado com seta ↑) na barra do Safari;\n' +
      '2) Escolha "Adicionar à Tela de Início";\n' +
      '3) Toque em "Adicionar".\n\n' +
      'O app vai aparecer como um ícone na sua tela inicial.'
    );
  } else {
    alert(
      'Para instalar no computador ou celular (Chrome/Edge):\n\n' +
      '• No celular: menu ⋮ do navegador → "Instalar app" / "Adicionar à tela inicial".\n' +
      '• No computador: ícone de instalar (⊕) na barra de endereço, ou menu ⋮ → "Instalar".'
    );
  }
}

// ═══════════════════ ETAPA 1 — Tipo + Modo de Busca ═════════

function selecionarTipo(tipo) {
  ESTADO.tipo = tipo;
  document.getElementById('card-catmat').classList.toggle('selecionado', tipo === 'material');
  document.getElementById('card-catser').classList.toggle('selecionado', tipo === 'servico');
  document.getElementById('label-tipo').textContent = tipo === 'material' ? 'produto' : 'serviço';
  document.getElementById('card-formulario').style.display = '';
  document.getElementById('nome-item').focus();
}

function selecionarModoBusca(modo) {
  ESTADO.modoBusca = modo;
  document.getElementById('modo-nome').classList.toggle('selecionado', modo === 'nome');
  document.getElementById('modo-descricao').classList.toggle('selecionado', modo === 'descricao');

  const grupoNome       = document.getElementById('grupo-nome');
  const grupoDescExtra  = document.getElementById('grupo-descricao-extra');
  const grupoDescLivre  = document.getElementById('grupo-descricao-livre');

  if (modo === 'nome') {
    grupoNome.classList.remove('hidden');
    grupoDescExtra.classList.remove('hidden');
    grupoDescLivre.classList.add('hidden');
    document.getElementById('nome-item').focus();
  } else {
    grupoNome.classList.add('hidden');
    grupoDescExtra.classList.add('hidden');
    grupoDescLivre.classList.remove('hidden');
    document.getElementById('descricao-livre').focus();
  }
}

async function buscarCatmat() {
  if (!ESTADO.tipo) { toast('Selecione Produto ou Serviço primeiro', 'warn'); return; }

  let query = '';
  let descricao = '';

  if (ESTADO.modoBusca === 'nome') {
    query = document.getElementById('nome-item').value.trim();
    descricao = document.getElementById('descricao-item').value.trim();
    if (!query) { toast('Digite o nome do produto/serviço', 'warn'); return; }
  } else {
    // Modo descrição: usa a descrição livre como query principal
    descricao = document.getElementById('descricao-livre').value.trim();
    if (!descricao) { toast('Digite a descrição do que você precisa', 'warn'); return; }
    query = descricao.split(/\s+/).slice(0, 4).join(' '); // primeiras 4 palavras como query
  }

  irParaEtapa(2);
  mostrarLoading('loading-cat', true);
  document.getElementById('resultado-list').innerHTML = '';
  document.getElementById('sem-resultados-cat').classList.add('hidden');
  document.getElementById('btn-confirmar-cat').disabled = true;

  try {
    const data = await fetchJson(`${API}/api/catmat/buscar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tipo: ESTADO.tipo, query, descricao }),
    });
    renderizarResultadosCat(data.resultados, query);

  } catch (e) {
    toast(e.message, 'error');
    voltarEtapa(1);
  } finally {
    mostrarLoading('loading-cat', false);
  }
}

async function buscarPorDescricaoFallback() {
  const desc = document.getElementById('descricao-fallback').value.trim();
  if (!desc) { toast('Digite uma descrição para buscar', 'warn'); return; }

  mostrarLoading('loading-cat', true);
  document.getElementById('sem-resultados-cat').classList.add('hidden');
  document.getElementById('resultado-list').innerHTML = '';
  document.getElementById('btn-confirmar-cat').disabled = true;

  try {
    const query = desc.split(/\s+/).slice(0, 4).join(' ');
    const data = await fetchJson(`${API}/api/catmat/buscar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ tipo: ESTADO.tipo, query, descricao: desc }),
    });
    renderizarResultadosCat(data.resultados, desc);
  } catch (e) {
    toast(e.message, 'error');
  } finally {
    mostrarLoading('loading-cat', false);
  }
}

function renderizarResultadosCat(resultados, queryOriginal) {
  const container = document.getElementById('resultado-list');
  const semRes    = document.getElementById('sem-resultados-cat');
  const badge     = document.getElementById('badge-tipo');

  badge.textContent = ESTADO.tipo === 'material' ? 'CATMAT' : 'CATSER';
  badge.className   = `badge badge-${ESTADO.tipo === 'material' ? 'catmat' : 'catser'}`;

  // Filtra itens sem código real ou com score zero
  const validos = (resultados || []).filter(r => r.codigo && r.codigo !== '—' && r.score > 0);

  if (!validos.length) {
    container.innerHTML = '';
    semRes.classList.remove('hidden');
    return;
  }

  semRes.classList.add('hidden');
  container.innerHTML = validos.map((r, i) => {
    const score      = Math.round((r.score || 0) * 100);
    const scoreClass = score >= 70 ? 'score-alto' : score >= 40 ? 'score-medio' : 'score-baixo';
    return `
      <div class="resultado-item" id="res-${i}"
           onclick="selecionarResultado(${i}, '${escapeAttr(r.codigo)}', '${escapeAttr(r.descricao)}', '${escapeAttr(r.tipo)}')">
        <span class="resultado-codigo">${r.codigo || '—'}</span>
        <div class="resultado-info">
          <div class="resultado-desc">${escapeHtml(r.descricao)}</div>
          <div class="resultado-meta">Fonte: ${r.fonte || '—'}</div>
        </div>
        <span class="resultado-score ${scoreClass}">${score}%</span>
      </div>`;
  }).join('');
}

function selecionarResultado(idx, codigo, descricao, tipo) {
  document.querySelectorAll('.resultado-item').forEach(el => el.classList.remove('selecionado'));
  document.getElementById(`res-${idx}`).classList.add('selecionado');
  ESTADO.itemSelecionado = { codigo, descricao, tipo };
  document.getElementById('btn-confirmar-cat').disabled = false;
}

function confirmarCatmat() {
  if (!ESTADO.itemSelecionado) { toast('Selecione um item', 'warn'); return; }
  irParaEtapa(3);
  renderizarCardItemSelecionado();
}

function renderizarCardItemSelecionado() {
  const item = ESTADO.itemSelecionado;
  document.getElementById('card-item-selecionado').innerHTML = `
    <div class="card-title"><span class="icon">✅</span> Item Selecionado</div>
    <div style="display:flex;align-items:center;gap:14px">
      <span class="badge badge-${item.tipo === 'CATMAT' ? 'catmat' : 'catser'}"
            style="font-size:.85rem;padding:6px 14px">${item.tipo}</span>
      <div>
        <div style="font-weight:700;font-size:.95rem">${escapeHtml(item.descricao)}</div>
        <div style="font-size:.8rem;color:var(--texto-leve);margin-top:3px">Código: ${item.codigo}</div>
      </div>
    </div>`;
}

// ═══════════════════ ETAPA 3 — Raio e tipo de busca ══════════

function selecionarRaio(km) {
  ESTADO.raioKm = km;
  document.querySelectorAll('.raio-btn').forEach(btn => {
    btn.classList.toggle('selecionado', parseInt(btn.dataset.km) === km);
  });
}

function selecionarTipoBusca(tipo) {
  ESTADO.tipoBusca = tipo;
  document.getElementById('card-exata').classList.toggle('selecionado', tipo === 'exata');
  document.getElementById('card-similar').classList.toggle('selecionado', tipo === 'similaridade');
}



// ═══════════════════ ETAPA 4 — Busca de empresas ═════════════

async function buscarEmpresas() {
  const cidade = document.getElementById('cidade-input').value.trim();
  const estado = document.getElementById('estado-input').value.trim();

  // Rede de segurança: não inicia a busca se faltarem dados obrigatórios.
  // (O botão já fica desabilitado; isto cobre chamadas por teclado/Enter.)
  if (!estado && !cidade) { toast('Selecione o estado e a cidade antes de buscar', 'warn'); validarFormEmpresas(); return; }
  if (!estado) { toast('Selecione o estado antes de buscar', 'warn'); validarFormEmpresas(); return; }
  if (!cidade) { toast('Informe a cidade antes de buscar', 'warn'); validarFormEmpresas(); return; }

  // Busca em TODAS as fontes disponíveis automaticamente. A chave do Google é
  // opcional: se existir, entra na busca; se não, o backend usa só o que puder.
  const mapsKey = localStorage.getItem('maps_key') || '';
  const fonte   = 'ambas';

  irParaEtapa(4);
  mostrarLoading('loading-emp', true);
  document.getElementById('resultados-wrapper').classList.add('hidden');

  const item = ESTADO.itemSelecionado;
  const corpo = {
    nome_item:   item.descricao,
    codigo_cat:  item.codigo,
    tipo_cat:    item.tipo,
    cidade,
    estado,
    raio_km:     ESTADO.raioKm,
    tipo_busca:  ESTADO.tipoBusca,
    fontes:      fonte,
    maps_key:    mapsKey,
  };

  // Busca com tempo generoso (o backend já limita cada fonte internamente, então
  // o total é controlado). Se mesmo assim estourar, tenta MAIS UMA VEZ antes de
  // desistir — buscas pesadas às vezes precisam de um 2º ciclo (cache aquecido).
  const TEMPO_MAX = 90000;      // 90s por tentativa
  const MAX_TENTATIVAS = 2;

  function _loadingTxt(txt) {
    const el = document.getElementById('loading-emp-text');
    if (el) el.textContent = txt;
  }

  try {
    let data = null;
    for (let tentativa = 1; tentativa <= MAX_TENTATIVAS; tentativa++) {
      if (tentativa > 1) {
        _loadingTxt('A busca foi grande e demorou — tentando novamente (pode levar um pouco)...');
      }
      const controller = new AbortController();
      const timeoutId  = setTimeout(() => controller.abort(), TEMPO_MAX);
      try {
        const resp = await fetch(`${API}/api/empresas/buscar`, {
          method: 'POST',
          signal: controller.signal,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(corpo),
        });
        clearTimeout(timeoutId);

        if (!resp.ok) {
          const err    = await resp.json().catch(() => ({}));
          const detail = (err.detail || '').toLowerCase();
          if (detail.includes('api key') || detail.includes('chave') || detail.includes('configurada')) {
            toast('❌ Google Maps API Key inválida. Verifique em ⚙️ Configurações.', 'error');
            abrirModalConfig();
          } else if (detail.includes('não encontrada') || detail.includes('cidade')) {
            toast(`❌ Cidade "${cidade}" não encontrada. Verifique o nome e o estado.`, 'error');
          } else {
            toast(`Erro: ${err.detail || 'Falha ao buscar empresas.'}`, 'error');
          }
          return;
        }
        data = await resp.json();
        break;  // sucesso
      } catch (e) {
        clearTimeout(timeoutId);
        if (e.name === 'AbortError' && tentativa < MAX_TENTATIVAS) {
          continue;  // tempo estourou → tenta de novo (próximo ciclo)
        }
        throw e;    // esgotou tentativas (ou erro diferente) → trata abaixo
      }
    }

    ESTADO.empresas     = data.empresas || [];
    ESTADO.fontesUsadas = data.fontes_usadas || [];
    ESTADO.dadosBusca   = { cidade, estado, raio: ESTADO.raioKm, tipo_busca: ESTADO.tipoBusca, fonte };
    // Mostra avisos de diagnóstico (ex.: falha ao consultar a Receita), mas
    // oculta os marcados como [interno] (ex.: Google não configurado).
    (data.avisos || [])
      .filter(a => !/^\[interno\]/.test(a))
      .forEach(a => toast(a, a.includes('⚠️') ? 'warn' : 'info'));
    renderizarEmpresas(ESTADO.empresas, cidade, ESTADO.raioKm);

  } catch (e) {
    if (e.name === 'AbortError') {
      toast('⏱️ A busca demorou mais que o esperado. Tente um raio menor ou tente de novo em instantes.', 'error');
    } else {
      toast(`Erro: ${e.message}`, 'error');
    }
    console.error(e);
  } finally {
    _loadingTxt('Buscando empresas na região...');
    mostrarLoading('loading-emp', false);
    document.getElementById('resultados-wrapper').classList.remove('hidden');
  }
}

function renderizarEmpresas(empresas, cidade, raio) {
  const grid    = document.getElementById('empresa-grid');
  const semRes  = document.getElementById('sem-resultados');
  const info    = document.getElementById('resultados-info');
  const resumo  = document.getElementById('resumo-busca');
  const item    = ESTADO.itemSelecionado;

  info.textContent = `${empresas.length} empresa(s) encontrada(s)`;
  resumo.innerHTML = `
    <strong>Item:</strong> ${escapeHtml(item.descricao)} &nbsp;·&nbsp;
    <strong>Código ${item.tipo}:</strong> ${item.codigo} &nbsp;·&nbsp;
    <strong>Cidade:</strong> ${escapeHtml(cidade)} &nbsp;·&nbsp;
    <strong>Raio:</strong> ${raio} km &nbsp;·&nbsp;
    <strong>Busca:</strong> ${ESTADO.tipoBusca}
  `;

  if (!empresas.length) {
    grid.innerHTML = '';
    document.getElementById('filtros-empresas')?.classList.add('hidden');
    semRes.classList.remove('hidden');
    return;
  }
  semRes.classList.add('hidden');

  // Guarda a lista completa para os filtros/ordenação atuarem sobre ela
  ESTADO.empresasBase = empresas.slice();
  montarBarraFiltros();
  renderGridEmpresas();
}

// Nome a exibir (prioriza fantasia; mantém razão social como subtítulo)
function nomeFantasia(emp) {
  const fant = (emp.nome_fantasia || '').trim();
  const nome = (emp.nome || '').trim();
  const razao = (emp.razao_social || '').trim();
  // emp.nome vem como fantasia-ou-razão do backend; preferimos a fantasia real
  return fant || nome || razao || 'Empresa';
}

// ── Barra de filtros/ordenação (montada uma vez por busca) ──
function montarBarraFiltros() {
  const barra = document.getElementById('filtros-empresas');
  if (!barra) return;
  barra.classList.remove('hidden');
  // valores atuais (preserva se o usuário já tinha escolhido)
  const ordem   = ESTADO.filtros?.ordem   || 'relevancia';
  const soTel   = ESTADO.filtros?.soTelefone || false;
  const chance  = ESTADO.filtros?.chanceMin || 0;
  const temSancionadas = (ESTADO.empresasBase || []).some(e => e.sancionada);
  barra.innerHTML = `
    <div class="filtro-item">
      <label>Ordenar por</label>
      <select id="f-ordem" onchange="aplicarFiltros()">
        <option value="relevancia" ${ordem==='relevancia'?'selected':''}>Chance de ter o produto (maior)</option>
        <option value="alfabetica" ${ordem==='alfabetica'?'selected':''}>Ordem alfabética (A–Z)</option>
        <option value="distancia"  ${ordem==='distancia'?'selected':''}>Distância (mais perto)</option>
      </select>
    </div>
    <div class="filtro-item">
      <label>Chance mínima</label>
      <select id="f-chance" onchange="aplicarFiltros()">
        <option value="0"  ${chance===0?'selected':''}>Todas</option>
        <option value="40" ${chance===40?'selected':''}>≥ 40%</option>
        <option value="70" ${chance===70?'selected':''}>≥ 70%</option>
      </select>
    </div>
    <label class="filtro-check">
      <input type="checkbox" id="f-tel" ${soTel?'checked':''} onchange="aplicarFiltros()">
      Só com telefone
    </label>
    <label class="filtro-check">
      <input type="checkbox" id="f-forneceu" ${ESTADO.filtros?.soForneceu?'checked':''} onchange="aplicarFiltros()">
      Só quem já forneceu
    </label>
    ${temSancionadas ? `<label class="filtro-check">
      <input type="checkbox" id="f-sancao" ${ESTADO.filtros?.ocultarSancionadas?'checked':''} onchange="aplicarFiltros()">
      Ocultar impedidas
    </label>` : ''}
    <div class="filtro-contagem" id="filtro-contagem"></div>
  `;
}

function aplicarFiltros() {
  ESTADO.filtros = {
    ordem:      document.getElementById('f-ordem')?.value || 'relevancia',
    chanceMin:  parseInt(document.getElementById('f-chance')?.value || '0', 10),
    soTelefone: document.getElementById('f-tel')?.checked || false,
    soForneceu: document.getElementById('f-forneceu')?.checked || false,
    ocultarSancionadas: document.getElementById('f-sancao')?.checked || false,
  };
  renderGridEmpresas();
}

function renderGridEmpresas() {
  const grid = document.getElementById('empresa-grid');
  const base = ESTADO.empresasBase || [];
  const f = ESTADO.filtros || { ordem: 'relevancia', chanceMin: 0, soTelefone: false };

  let lista = base.filter(emp => {
    if (f.soTelefone && !(emp.telefone && emp.telefone.trim())) return false;
    if (f.soForneceu && !emp.ja_forneceu) return false;
    if (f.ocultarSancionadas && emp.sancionada) return false;
    if (f.chanceMin && Math.round(emp.relevancia || 0) < f.chanceMin) return false;
    return true;
  });

  const colator = (a, b) => (a || '').localeCompare(b || '', 'pt-BR', { sensitivity: 'base' });
  if (f.ordem === 'alfabetica') {
    lista.sort((a, b) => colator(nomeFantasia(a), nomeFantasia(b)));
  } else if (f.ordem === 'distancia') {
    lista.sort((a, b) => {
      const da = a.distancia_km ?? Infinity, db = b.distancia_km ?? Infinity;
      return da - db;
    });
  } else { // relevancia
    lista.sort((a, b) => (b.relevancia || 0) - (a.relevancia || 0));
  }

  const cont = document.getElementById('filtro-contagem');
  if (cont) cont.textContent = `${lista.length} de ${base.length}`;

  // A lista exibida é a que será exportada/salva
  ESTADO.empresas = lista;

  if (!lista.length) {
    grid.innerHTML = `<div class="card text-center" style="padding:32px;color:var(--texto-leve)">
      Nenhuma empresa atende aos filtros selecionados.</div>`;
    return;
  }

  grid.innerHTML = lista.map(emp => cardEmpresa(emp)).join('');
}

function cardEmpresa(emp) {
  const rel = Math.round(emp.relevancia || 0);
  const relClass = rel >= 70 ? 'rel-alto' : rel >= 40 ? 'rel-medio' : 'rel-baixo';
  const relRotulo = rel >= 70 ? 'alta' : rel >= 40 ? 'média' : 'baixa';

  const telRaw = (emp.telefone || '').trim();
  const telDigits = telRaw.replace(/\D/g, '');
  const stars = emp.avaliacao_google ? `⭐ ${Number(emp.avaliacao_google).toFixed(1)}` : '';
  const tel2Digits = (emp.telefone2 || '').replace(/\D/g, '');
  const tel2  = tel2Digits ? `<a href="tel:${tel2Digits}" class="emp-link">📞 ${escapeHtml(emp.telefone2)}</a>` : '';
  const wpp   = emp.whatsapp ? `<a href="https://wa.me/${emp.whatsapp.replace(/\D/g,'')}" target="_blank" class="emp-link">💬 WhatsApp</a>` : '';
  const site  = emp.site     ? `<a href="${encodeURI(emp.site)}" target="_blank" class="emp-link">🌐 Site</a>` : '';
  const email = emp.email    ? `<a href="mailto:${escapeAttr(emp.email)}" class="emp-link emp-link-secundario">✉️ ${escapeHtml(emp.email)}</a>` : '';

  // Telefone EM EVIDÊNCIA (bloco destacado + clicável)
  const telBloco = telDigits
    ? `<a href="tel:${telDigits}" class="empresa-telefone"><span class="tel-icone">📞</span>
         <span class="tel-numero">${escapeHtml(telRaw)}</span>
         <span class="tel-acao">Ligar</span></a>`
    : `<div class="empresa-telefone sem-telefone">📵 Sem telefone cadastrado</div>`;

  // Nome fantasia em destaque; razão social como subtítulo (se diferente)
  const nome = nomeFantasia(emp);
  const razao = (emp.razao_social || '').trim();
  const subRazao = (razao && razao.toLowerCase() !== nome.toLowerCase())
    ? `<div class="empresa-razao">Razão social: ${escapeHtml(razao)}</div>` : '';

  const distBadge = (emp.distancia_km !== null && emp.distancia_km !== undefined)
    ? `<span class="emp-dist">📏 ${Number(emp.distancia_km).toFixed(1)} km</span>` : '';
  const fornecBadge = emp.ja_forneceu
    ? `<span class="emp-forneceu" title="Já forneceu este tipo de item ao poder público">✓ já forneceu ao poder público</span>`
    : '';
  const sancaoBadge = emp.sancionada
    ? `<span class="emp-sancao" title="${escapeAttr(emp.sancao_detalhe || '')}">🚫 impedida de contratar (${escapeHtml(emp.sancao_detalhe || 'sancionada')})</span>`
    : '';

  const cnpjLinha = emp.cnpj
    ? `<div class="empresa-cnpj">🧾 CNPJ: ${escapeHtml(emp.cnpj)}${emp.situacao ? ` · <span class="emp-situacao">${escapeHtml(emp.situacao)}</span>` : ''}</div>` : '';
  const cnaeLinha = emp.cnae_principal
    ? `<div class="empresa-cnae">🏷️ CNAE ${escapeHtml(emp.cnae_principal)}${emp.cnae_descricao ? ` — ${escapeHtml(emp.cnae_descricao)}` : ''}</div>` : '';
  const horarioLinha = emp.horario
    ? `<div class="empresa-horario">🕒 ${escapeHtml(emp.horario)}</div>` : '';
  // Indicador de origem/complemento pelo mapa (OpenStreetMap)
  const osmBadge = emp.fonte === 'osm'
    ? `<span class="emp-mapa" title="Estabelecimento encontrado no mapa (OpenStreetMap)">🗺️ do mapa</span>`
    : (emp.enriquecido_osm
        ? `<span class="emp-mapa" title="Contato complementado pelo mapa (OpenStreetMap)">🗺️ contato do mapa</span>`
        : '');

  const extras = [tel2, wpp, site, email].filter(Boolean).join('');

  return `
    <div class="empresa-card">
      <div class="empresa-header">
        <div>
          <div class="empresa-nome">${escapeHtml(nome)} ${distBadge} ${osmBadge}</div>
          ${fornecBadge}
          ${sancaoBadge}
          ${subRazao}
        </div>
        <div class="empresa-rel ${relClass}" title="Chance de ter o produto: ${relRotulo}">${rel}%</div>
      </div>
      <div class="empresa-endereco">📍 ${escapeHtml(emp.endereco || '—')}</div>
      ${cnpjLinha}
      ${cnaeLinha}
      ${horarioLinha}
      ${stars ? `<div class="empresa-avaliacao">${stars} (${emp.total_avaliacoes || 0} avaliações)</div>` : ''}
      ${telBloco}
      ${extras ? `<div class="empresa-contatos">${extras}</div>` : ''}
      ${emp.descricao ? `<div class="empresa-desc">${escapeHtml(emp.descricao)}</div>` : ''}
    </div>`;
}

// ═══════════════════ EXPORTAR ════════════════════════════════

async function exportar(formato) {
  if (!ESTADO.empresas.length) { toast('Nenhuma empresa para exportar', 'warn'); return; }
  const item = ESTADO.itemSelecionado;
  toast(`Gerando ${formato.toUpperCase()}...`, 'info');
  try {
    const resp = await fetch(`${API}/api/export/${formato}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        empresas:     ESTADO.empresas,
        titulo:       item.descricao,
        codigo_cat:   item.codigo,
        tipo_cat:     item.tipo,
        cidade:       ESTADO.dadosBusca?.cidade || '',
        raio_km:      ESTADO.dadosBusca?.raio   || ESTADO.raioKm,
      }),
    });
    if (!resp.ok) throw new Error('Erro ao gerar arquivo');
    const blob = await resp.blob();
    const url  = URL.createObjectURL(blob);
    const a    = document.createElement('a');
    a.href     = url;
    a.download = `orcamentos_${formato === 'excel' ? 'xlsx' : 'pdf'}`;
    a.click();
    URL.revokeObjectURL(url);
    toast(`${formato.toUpperCase()} baixado com sucesso!`, 'success');
  } catch (e) {
    toast(`Erro ao exportar: ${e.message}`, 'error');
  }
}

// ═══════════════════ SALVAR / CARREGAR BUSCAS ════════════════

async function salvarBusca() {
  if (!ESTADO.empresas.length) { toast('Nenhuma busca para salvar', 'warn'); return; }
  const item = ESTADO.itemSelecionado;
  try {
    const data = await fetchJson(`${API}/api/buscas/salvar`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        tipo:         item.tipo,
        catcodigo:    item.codigo,
        catdescricao: item.descricao,
        cidade:       ESTADO.dadosBusca?.cidade || '',
        estado:       ESTADO.dadosBusca?.estado || '',
        raio_km:      ESTADO.dadosBusca?.raio   || ESTADO.raioKm,
        tipo_busca:   ESTADO.tipoBusca,
        empresas:     ESTADO.empresas,
      }),
    });
    ESTADO.buscaId = data.id;
    toast('Busca salva com sucesso! 💾', 'success');
  } catch (e) {
    toast(`Erro ao salvar: ${e.message}`, 'error');
  }
}

async function abrirModalBuscasSalvas() {
  document.getElementById('modal-buscas').classList.add('aberto');
  history.pushState({ modal: 'modal-buscas', etapa: ESTADO.etapaAtual }, '', '#buscas');
  const container = document.getElementById('lista-buscas-salvas');
  container.innerHTML = '<div class="loading"><div class="spinner"></div><span>Carregando...</span></div>';
  try {
    const data   = await fetchJson(`${API}/api/buscas/listar`);
    const buscas = data.buscas || [];
    if (!buscas.length) {
      container.innerHTML = '<p style="text-align:center;padding:32px;color:var(--texto-leve)">Nenhuma busca salva.</p>';
      return;
    }
    container.innerHTML = buscas.map(b => `
      <div class="busca-salva-item">
        <div>
          <strong>${escapeHtml(b.catdescricao || b.nome_busca || 'Busca')}</strong>
          <small style="display:block;color:var(--texto-leve)">
            ${b.tipo} · ${b.cidade} · ${b.raio_km} km · ${b.total_empresas} empresa(s)
          </small>
        </div>
        <div style="display:flex;gap:8px">
          <button class="btn btn-primary" style="padding:6px 14px;font-size:.8rem"
                  onclick="carregarBusca(${b.id})">Carregar</button>
          <button class="btn btn-outline" style="padding:6px 10px;font-size:.8rem;color:var(--vermelho)"
                  onclick="excluirBusca(${b.id}, this)">✕</button>
        </div>
      </div>`).join('');
  } catch (e) {
    container.innerHTML = `<p style="color:var(--vermelho)">Erro ao carregar buscas: ${e.message}</p>`;
  }
}

async function carregarBusca(id) {
  try {
    const data  = await fetchJson(`${API}/api/buscas/${id}`);
    const busca = data.busca;

    ESTADO.itemSelecionado = {
      codigo:   busca.catcodigo,
      descricao: busca.catdescricao,
      tipo:     busca.tipo,
    };
    ESTADO.tipo      = busca.tipo === 'CATMAT' ? 'material' : 'servico';
    ESTADO.tipoBusca = busca.tipo_busca;
    ESTADO.raioKm    = busca.raio_km;
    ESTADO.empresas  = data.empresas.map(e => ({
      ...e,
      ...(e.dados_extra ? JSON.parse(e.dados_extra) : {}),
    }));
    ESTADO.dadosBusca = { cidade: busca.cidade, estado: busca.estado, raio: busca.raio_km, tipo_busca: busca.tipo_busca };

    renderizarCardItemSelecionado();
    irParaEtapa(4);
    mostrarLoading('loading-emp', false);
    document.getElementById('resultados-wrapper').classList.remove('hidden');
    renderizarEmpresas(ESTADO.empresas, busca.cidade, busca.raio_km);
    fecharModal('modal-buscas');
    toast('Busca carregada!', 'success');
  } catch (e) {
    toast(`Erro: ${e.message}`, 'error');
  }
}

async function excluirBusca(id, btn) {
  if (!confirm('Excluir esta busca?')) return;
  try {
    await fetch(`${API}/api/buscas/${id}`, { method: 'DELETE' });
    btn.closest('.busca-salva-item').remove();
    toast('Busca excluída', 'success');
  } catch (e) {
    toast('Erro ao excluir', 'error');
  }
}

// ═══════════════════ MODAL CONFIGURAÇÕES ═════════════════════

function abrirModalConfig() {
  document.getElementById('config-maps-key').value = localStorage.getItem('maps_key') || '';
  const backendInput = document.getElementById('config-backend-url');
  if (backendInput) {
    backendInput.value = localStorage.getItem('backend_url') || '';
  }
  document.getElementById('modal-config').classList.add('aberto');
  history.pushState({ modal: 'modal-config', etapa: ESTADO.etapaAtual }, '', '#config');
}

function salvarConfig() {
  const mapsKey = document.getElementById('config-maps-key').value.trim();
  const backendInput = document.getElementById('config-backend-url');

  if (backendInput) {
    const backendVal = backendInput.value.trim().replace(/\/+$/, '');
    if (backendVal) localStorage.setItem('backend_url', backendVal);
    else localStorage.removeItem('backend_url');
    API = obterApiUrl();
  }

  if (mapsKey && (!mapsKey.startsWith('AIza') || mapsKey.length < 35)) {
    toast('❌ Google Maps Key inválida! Deve começar com "AIza" e ter ~39 caracteres.', 'error');
    document.getElementById('config-maps-key').style.borderColor = 'var(--vermelho)';
    return;
  }
  document.getElementById('config-maps-key').style.borderColor = '';

  if (mapsKey) localStorage.setItem('maps_key', mapsKey);
  else         localStorage.removeItem('maps_key');

  fecharModal('modal-config');
  toast('✅ Configurações salvas!', 'success');
}

function carregarConfig() {
  // Keys são lidas no momento do uso
}

// ═══════════════════ NAVEGAÇÃO ENTRE ETAPAS ══════════════════

function irParaEtapa(etapa, pushState = true) {
  [1, 2, 3, 4].forEach(n => {
    const el = document.getElementById(`etapa-${n}`);
    if (el) el.classList.toggle('hidden', n !== etapa);
    const stepEl = document.getElementById(`step-${n}`);
    if (stepEl) {
      stepEl.classList.remove('ativo', 'concluido');
      if (n === etapa) stepEl.classList.add('ativo');
      else if (n < etapa) stepEl.classList.add('concluido');
    }
    const lineEl = document.getElementById(`line-${n}`);
    if (lineEl) lineEl.classList.toggle('ativa', n < etapa);
  });
  ESTADO.etapaAtual = etapa;
  if (pushState && (!history.state || history.state.etapa !== etapa || history.state.modal)) {
    history.pushState({ etapa: etapa }, '', '#etapa-' + etapa);
  }
  window.scrollTo({ top: 0, behavior: 'smooth' });
}

function voltarEtapa(etapa) {
  irParaEtapa(etapa);
}

// ═══════════════════ MODAIS ══════════════════════════════════

function fecharModal(id) {
  const el = document.getElementById(id);
  if (el) el.classList.remove('aberto');
  if (history.state && history.state.modal === id) {
    history.back();
  }
}

document.addEventListener('click', e => {
  if (e.target.classList.contains('modal-overlay') && e.target.classList.contains('aberto')) {
    fecharModal(e.target.id);
  }
});

// Listener popstate (History API): suporte ao botão voltar do celular/navegador
window.addEventListener('popstate', (e) => {
  document.querySelectorAll('.modal-overlay.aberto').forEach(m => m.classList.remove('aberto'));
  const etapa = e.state?.etapa || 1;
  if (etapa !== ESTADO.etapaAtual) {
    irParaEtapa(etapa, false);
  }
});

// ═══════════════════ LOADING ═════════════════════════════════

function mostrarLoading(id, show) {
  document.getElementById(id).classList.toggle('hidden', !show);
}

// ═══════════════════ TOAST ═══════════════════════════════════

function toast(msg, tipo = 'info') {
  const container = document.getElementById('toast-container');
  const el = document.createElement('div');
  el.className = `toast toast-${tipo}`;
  const icons = { success: '✅', error: '❌', warn: '⚠️', info: 'ℹ️' };
  el.innerHTML = `<span>${icons[tipo] || 'ℹ️'}</span> ${msg}`;
  container.appendChild(el);
  setTimeout(() => el.classList.add('show'), 10);
  setTimeout(() => {
    el.classList.remove('show');
    setTimeout(() => el.remove(), 400);
  }, 4500);
}

// ═══════════════════ UTILITÁRIOS ═════════════════════════════

function escapeHtml(str) {
  return String(str || '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}
function escapeAttr(str) {
  return String(str || '').replace(/'/g, "\\'").replace(/"/g, '\\"');
}
