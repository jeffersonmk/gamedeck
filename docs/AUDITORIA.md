# GameDeck — Auditoria, avaliação tecnológica e plano de refatoração

> Base auditada: `main` em `d56323e` (idêntico a `kra-mo/cartridges` `main`:
> 0 commits à frente, 0 atrás). Data: 2026-10-13.
> Medições: `tests/bench/bench.py` (veja `tests/bench/README.md`), build
> release, Python 3.14, GTK 4.24, Libadwaita 1.10, CachyOS, 12 CPUs.

## 1. Diagnóstico da arquitetura original

**Stack:** Python 3 + PyGObject, GTK 4 ≥ 4.15, Libadwaita ≥ 1.6, interface em
Blueprint (subprojeto `blueprint-compiler` v0.16), build Meson, dependências
Python `requests`, `Pillow`, `PyYAML`. Distribuição: Flatpak (GNOME 49),
Windows (Inno Setup/MSYS2), macOS (PyInstaller). ~6,9 mil linhas de Python,
42 traduções.

**Componentes**

| Componente | Responsabilidade |
|---|---|
| `main.py` (`CartridgesApplication`) | ciclo de vida, ações, carga da biblioteca do disco, busca/lançamento via CLI |
| `window.py` | `Gtk.FlowBox` com **um widget por jogo**, filtro/ordenação, barra lateral |
| `game.py` | `Game` = **modelo e widget ao mesmo tempo** (`Gtk.Box` com template) |
| `game_cover.py` | textura, GIF animado, versão desfocada da capa |
| `store/` | `Store` (índice de jogos) + `Pipeline` de *managers* por jogo |
| `store/managers/` | Display (UI), File (JSON), Cover, SteamAPI, SGDB |
| `importer/` | 9 fontes (Steam, Lutris, Heroic, Bottles, itch, Legendary, RetroArch, Flatpak, Desktop) executadas em `Gio.Task` |
| `utils/` | SteamGridDB, API Steam, rate limiter, conversão de capas, sqlite, execução |
| `search-provider/` | provedor de busca do GNOME Shell (processo separado) |

**Fluxo de inicialização:** `do_activate` → lê todos os `games/*.json` → para
cada um cria o widget `Game` (template GTK), decodifica a capa e adiciona ao
`FlowBox` — tudo **síncrono na thread principal**.

## 2. Comparação `main` × `rewrite` (upstream)

`rewrite` **continua em Python** (não é Rust). É uma reescrita com versão 49.0,
~3 mil linhas.

| | `main` | `rewrite` |
|---|---|---|
| Modelo × widget | `Game` é um `Gtk.Box` | `Game` é `GObject` puro; widgets separados |
| Grade | `FlowBox` (1 widget por jogo, sem virtualização) | `Gtk.GridView` (virtualizada) |
| Concorrência | `Gio.Task` + threads | `asyncio` integrado ao GLib (`asyncio.to_thread`) |
| Fontes | 9 | 6 (Steam, Lutris, Heroic, itch, Legendary, Desktop) + "Imported" |
| Ausente no `rewrite` | — | Bottles, RetroArch, Flatpak, **SteamGridDB**, diálogo de preferências, remoção de jogos ausentes, provedor de busca do Shell, empacotamento Windows/macOS, **todas as traduções** (`po/` só tem `LINGUAS`/`POTFILES`) |
| Novo no `rewrite` | — | Coleções, suporte a controle (libmanette), `--filesystem=host:ro` |
| Estado | estável | incompleto; último commit em abr/2026; o upstream declarou o projeto **sem manutenção ativa** |

**Conclusão:** o `rewrite` acerta na arquitetura (modelo separado + `GridView`),
mas está longe da paridade de funcionalidades. Migrar para ele hoje removeria
fontes, SGDB, preferências e traduções. A estratégia recomendada é **trazer as
ideias do `rewrite` para o `main` aos poucos**, não trocar de branch.

## 3. Problemas encontrados

Legenda: **[C]** comprovado por código, **[M]** comprovado por medição,
**[H]** hipótese ainda não verificada.

| # | Problema | Evidência | Impacto | Status |
|---|---|---|---|---|
| 1 | `Store.get()` percorria todos os jogos a cada busca → carga da biblioteca O(n²) | [C] `store.py`; [M] 6 milhões de iterações em 2000 jogos | abertura lenta em bibliotecas grandes | **corrigido** |
| 2 | `filter_func` chamava `set_library_child()` (que varre a biblioteca) **para cada jogo** → busca O(n²) | [C] `window.py`; [M] 795 ms por tecla com 2000 jogos | busca trava a UI | **corrigido** |
| 3 | `CoverManager` era *blocking*: download (timeout 5 s, até 3 tentativas com `sleep(3)`) e recodificação de imagens **na thread principal** | [C] `manager.py: blocking=True`; [M] travamentos de 2,6 s na importação | UI congela ao importar; até ~30 s por jogo com rede ruim | **corrigido** |
| 4 | `create_source_rows()` (varre todos os jogos) chamado a cada jogo atualizado | [C] `display_manager.py` | O(n²) ao atualizar muitos jogos | **corrigido** (coalescido) |
| 5 | Vazamento de arquivos temporários: 2 por capa importada, nunca apagados | [C] `Gio.File.new_tmp` sem remoção; [M] 600 arquivos em `/tmp` para 300 jogos | `/tmp` (tmpfs = RAM) cresce a cada importação | **corrigido** |
| 6 | `save_cover` mexia em widgets a partir de threads (SGDB já rodava em thread) | [C] `sgdb_manager.py` → `save_cover` → `new_cover` | GTK não é thread-safe: possíveis travamentos aleatórios | **corrigido** |
| 7 | Primeiro quadro de GIF definido a partir de uma thread de trabalho | [C] `game_cover.py: run_in_thread(update_animation)` | idem | **corrigido** |
| 8 | Cada `add_picture()` (abrir detalhes de um jogo com GIF) iniciava **mais uma** cadeia de timers que nunca parava | [C]; [M] teste de regressão | animação acelera e CPU cresce com o uso | **corrigido** |
| 9 | Escrita de JSON sem fechar o arquivo e não atômica | [C] `json.dump(..., path.open("w"))` | queda/disco cheio corrompe o jogo | **corrigido** (escrita atômica) |
| 10 | Um JSON inválido (UTF-8 ruim, lista, chave faltando) derrubava a carga | [C] `except` só cobria `JSONDecodeError`; `Game()` sem validação | biblioteca não abre | **corrigido** |
| 11 | Uma capa corrompida impedia o jogo de aparecer | [C] `Gdk.Texture.new_from_filename` sem `try` dentro do `DisplayManager` | jogo some da grade | **corrigido** |
| 12 | Uma conexão `schema.changed` **por jogo**, nunca desconectada | [C] `game.py` | jogos removidos ficam vivos na memória; custo O(n) a cada mudança de config | **corrigido** (1 handler) |
| 13 | 2 linhas de log DEBUG por jogo gravadas em arquivo a cada abertura | [M] 0,22 s / 2000 jogos | abertura | **corrigido** (1 linha de resumo) |
| 14 | Widgets `Game` são **criados em threads de importação** | [C] fontes instanciam `Game` (um `Gtk.Box`) dentro de `Gio.Task.run_in_thread` | viola as regras de thread do GTK | **pendente** — exige separar modelo e widget (item 1 do roadmap) |
| 15 | Grade sem virtualização: 1 widget + 1 textura por jogo | [M] 682 MB de RSS com 2000 jogos; ~1,3 s de layout inicial | memória e abertura escalam linearmente | **pendente** — `GridView` (roadmap) |
| 16 | Flatpak com `--filesystem=host` (leitura **e escrita**) | [C] manifesto; o `rewrite` já usa `host:ro` | permissão maior que o necessário | **pendente** — recomendação, não testado aqui |
| 17 | Jogos são lançados com `flatpak-spawn --host sh -c` | [C] `run_executable.py` | o sandbox é contornado por design (necessário para abrir jogos do host); comandos vêm de arquivos locais do usuário | aceito (documentado) |
| 18 | `set_texture` faz `discard(<gerador>)`, que não remove nada | [C] `game_cover.py` | o descarte de imagens invisíveis nunca funcionou | **não alterado** — "consertar" mudaria comportamento sem teste visual |
| 19 | `retryable_on` usa `ConnectionError` do Python e compara tipo exato | [C] | erros de conexão do `requests` não são repetidos | [H] impacto real não medido |
| 20 | Capa escolhida no diálogo de detalhes deixa 1 temporário | [C] `details_dialog.py` | pequeno, uma vez por ação do usuário | pendente (baixo) |

**Segurança/empacotamento:** sem segredos no código; chave da SteamGridDB fica
no GSettings. Entradas de `.desktop` são passadas com `shlex.quote`. O CI do
Flatpak usa a imagem `gnome-47` enquanto o manifesto pede o runtime 49 [C].

## 4. Avaliação tecnológica

| Critério | A. Python refatorado | B. Rust (migração progressiva) | C. Híbrido Python + Rust |
|---|---|---|---|
| Abertura/responsividade | Gargalos medidos eram **algorítmicos e de thread**, não da linguagem: corrigidos em Python (-45% abertura, -98% busca) | Ganho adicional pequeno enquanto a grade não for virtualizada | Igual a A para a UI |
| RAM | Dominada por texturas e widgets GTK (C), não por objetos Python. `GridView` resolve em qualquer linguagem | Sem interpretador (economia estimada de algumas dezenas de MB, não medida) | Pior: Python + runtime Rust |
| Bibliotecas grandes | Com índice O(1) e `GridView`, o custo Python por jogo é µs | Melhor em teoria; diferença irrelevante até dezenas de milhares de jogos | — |
| Busca/metadados | 13 ms em 2000 jogos após a correção | Mais rápido, mas já abaixo de 1 quadro | Não justifica uma ponte FFI |
| GTK/Libadwaita/GNOME | PyGObject: suporte de primeira linha, Blueprint funciona | gtk4-rs/libadwaita-rs maduros; templates Blueprint funcionam | Duas cadeias de build (Meson + Cargo) |
| Manutenção | Base atual, mesmo idioma do upstream e do `rewrite` | Reescrita de ~7 mil linhas + 9 integrações | Maior complexidade (PyO3/GObject-introspection, empacotamento) |
| Testes/depuração | Simples; 18 testes adicionados rodam sem tela | Bom (cargo test), mas tudo precisa ser reescrito antes | Dois mundos para depurar |
| Integrações (Steam, Lutris, Heroic…) | Já existem e funcionam | Reescrever parsers VDF/SQLite/JSON/YAML | Nenhum componente tem custo de CPU que justifique |
| Flatpak | Igual hoje | Pacote menor (sem Python), mas o runtime GNOME já traz Python | Precisa do SDK Rust + módulos Python |
| Risco/custo | Baixo, incremental | Alto: meses, regressões, perda de traduções/fontes no caminho | Médio-alto sem benefício medido |

**Recomendação: A — continuar em Python, refatorando progressivamente em direção
à arquitetura do `rewrite` (modelo separado do widget + `Gtk.GridView`).**

Justificativa: todos os gargalos medidos tinham causa algorítmica ou de uso
incorreto da thread principal, e foram resolvidos em Python. O que sobra
(memória e layout inicial) vem de um widget + uma textura por jogo, um problema
de **arquitetura de UI** que existe igualmente em Rust. Nenhum componente
mostrou custo de CPU em Python que justifique FFI. Rust deve ser reconsiderado
apenas se, **depois** do `GridView`, uma medição mostrar um gargalo de CPU em
código Python (ex.: parsing de bibliotecas Steam enormes).

## 5. Plano priorizado

| Etapa | Mudança | Risco | Critério de conclusão |
|---|---|---|---|
| 1 ✅ | Correções de baixo risco (itens 1–13) | baixo | testes passam; benchmark sem regressão de tempo; 0 temporários |
| 2 | Separar `Game` (modelo `GObject`) de `GameWidget`; fontes criam só o modelo | médio | nenhum widget criado fora da thread principal (verificável com `assert` em debug); todas as 9 fontes importam |
| 3 | Trocar `FlowBox` por `Gtk.GridView` + `Gtk.FilterListModel`/`SortListModel` | médio | RSS com 2000 jogos < 300 MB; `covers_ready_s` < 1 s; navegação por teclado e leitor de tela preservados |
| 4 | Cache de texturas com limite (LRU por bytes) carregado sob demanda pelo `GridView` | baixo | memória estável com uso prolongado (abrir/fechar detalhes 1000×) |
| 5 | Flatpak: `--filesystem=host` → `host:ro`; CI para imagem GNOME 49 | baixo | build Flatpak + importação de todas as fontes + mudar capa funcionando |
| 6 | Avaliar Rust **somente** com gargalo medido | — | perfil mostrando > 100 ms de CPU Python em um caminho quente |

**Permanecem em Python:** tudo. **Candidatos a Rust:** nenhum com
justificativa mensurável hoje.

**Mitigação de regressões:** cada etapa em commit/branch separado; testes
automatizados para a lógica; benchmark antes/depois; teste manual no display
real com biblioteca isolada.

## 6. Resultados medidos (etapa 1)

Mediana de 5 execuções, mesma biblioteca sintética, build release.

**Biblioteca grande — 2000 jogos (1600 com capa), importação de 300 jogos Steam**

| Métrica | Antes | Depois | Variação |
|---|---:|---:|---:|
| `do_activate` (UI bloqueada) | 3,78 s | 1,96 s | **-48%** |
| Janela pronta | 4,00 s | 2,19 s | **-45%** |
| Todas as capas visíveis | 5,29 s | 3,49 s | **-34%** |
| Busca (1 tecla) | 795 ms | 13 ms | **-98%** |
| Ordenação | 53 ms | 38 ms | -30% |
| Importação total | 4,70 s | 1,85 s | **-61%** |
| Maior travamento durante importação | 2608 ms | 483 ms | **-81%** |
| Travamentos > 50 ms na importação | 4 | 9 | pior (mais curtos e distribuídos) |
| RSS após abrir | 682 MB | 682 MB | = |
| RSS após importar | 769 MB | 798 MB | +4% |
| Temporários deixados | 600 | 0 | **-100%** |

**Biblioteca pequena — 100 jogos, importação de 50**

| Métrica | Antes | Depois | Variação |
|---|---:|---:|---:|
| Janela pronta | 0,365 s | 0,360 s | = |
| Busca | 3,4 ms | 0,8 ms | -77% |
| Importação total | 0,78 s | 0,42 s | -46% |
| Maior travamento | 621 ms | 29 ms | **-95%** |
| RSS após importar | 146 MB | 177 MB | **+21%** (ver limitações) |
| Temporários deixados | 100 | 0 | -100% |

## 7. Limitações conhecidas

* **+25–30 MB de RSS após importar**: as capas agora são processadas em
  paralelo em threads. Com `MALLOC_ARENA_MAX=2` a diferença cai pela metade,
  ou seja, a maior parte são *arenas* do glibc por thread (não é vazamento).
  Limitar a concorrência a 4 foi testado e **não** reduziu a memória, então
  não foi mantido.
* Mais travamentos curtos (> 50 ms) na importação de 300 jogos: o trabalho saiu
  de um bloco único de 2,6 s para vários pedaços menores (contenção do GIL +
  criação de widgets). Resolve-se com as etapas 2 e 3.
* Itens 14–20 da tabela continuam abertos.
* Não medido aqui: partida a frio (exige root), Flatpak (exige SDK GNOME 49 e
  flatpak-builder) e renderização real por GPU (o benchmark usa Broadway).
  Procedimentos em `tests/bench/README.md`.
