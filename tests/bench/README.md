# Benchmark do GameDeck

`bench.py` mede o build **instalado** (via `meson install`) usando uma
biblioteca sintética e um ambiente isolado: HOME/XDG/TMPDIR falsos,
GSettings em memória e o backend Broadway do GTK (headless). Seus jogos,
configurações e a sua tela não são tocados. A rede é desativada (a chamada à
API da Steam é substituída por um stub).

## Como rodar

```sh
# 1. Instalar o build num prefixo local (perfil release)
meson setup _build --prefix="$PWD/_install" -Dprofile=release
meson install -C _build

# 2. Biblioteca pequena e grande (mediana de N execuções)
python3 tests/bench/bench.py --prefix _install --games 100  --import-games 50  --runs 5 --out small.json
python3 tests/bench/bench.py --prefix _install --games 2000 --import-games 300 --runs 5 --out large.json
```

Para comparar com outra versão, instale-a em outro prefixo (ex.: um
`git worktree` da versão anterior) e rode o mesmo comando com `--prefix`
apontando para ele. Use sempre o mesmo `--workdir` para reaproveitar a mesma
biblioteca sintética.

## Métricas

| Métrica | Significado |
|---|---|
| `activate_s` | Duração de `do_activate` (thread principal bloqueada carregando a biblioteca) |
| `ready_s` | Do início do processo até o loop principal ficar livre após apresentar a janela |
| `covers_ready_s` | Até todas as capas estarem decodificadas e aplicadas (inclui o primeiro layout) |
| `search_ms` / `sort_ms` | Tempo síncrono de refiltrar/reordenar a biblioteca (uma tecla na busca) |
| `rss_mb_*` | Memória residente após abrir / após importar |
| `import_total_s` | Importação Steam de N jogos com capas locais 600×900 |
| `import_max_gap_ms` | Maior travamento do loop principal durante a importação (UI congelada) |
| `import_gaps_50ms` | Quantos travamentos > 50 ms houve durante a importação |
| `tmp_files_left` | Arquivos temporários deixados para trás (vazamento) |

Variáveis de diagnóstico: `BENCH_PROFILE=1` (cProfile de `do_activate`),
`BENCH_STALLS=1` (amostra a pilha da thread principal quando ela trava > 100 ms
durante a importação), `BENCH_PROFILE_IMPORT=1` (cProfile da importação; no
Python ≥ 3.12 ele mistura threads, use com cuidado).

## Limitações

* **Partida a frio**: os números são com o cache de páginas do kernel quente.
  Para medir a frio, rode como root `sync; echo 3 > /proc/sys/vm/drop_caches`
  antes de cada execução (afeta o sistema inteiro).
* **Broadway ≠ Wayland**: o Broadway não pinta sem um cliente conectado, então
  o custo de renderização real (GPU) não entra na conta. Layout e alocação de
  widgets entram.
* **Flatpak**: não é medido aqui. Procedimento manual:
  ```sh
  flatpak install --user flathub org.gnome.Sdk//49 org.flatpak.Builder
  flatpak run org.flatpak.Builder --user --install --force-clean \
      .flatpak-build build-aux/flatpak/page.kramo.Cartridges.Devel.json
  flatpak run page.kramo.Cartridges.Devel
  ```
