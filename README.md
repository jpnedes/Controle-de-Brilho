# ☀️ Controle de Brilho

Controle o brilho de **monitores externos no Windows** via **DDC/CI**, o mesmo canal usado pelos botões físicos do monitor. Ajuste cada tela com controles deslizantes ou use **atalhos globais** para níveis pré-definidos. O programa fica na **bandeja do sistema** e **inicia junto com o Windows**.

<p>
  <img src="docs/janela.png" alt="Janela com controles deslizantes por monitor e botões de preset" height="260">
  &nbsp;&nbsp;
  <img src="docs/menu-bandeja.png" alt="Menu do ícone na bandeja do sistema" height="260">
</p>

## Recursos

- 🖥️ Um controle deslizante por monitor, mais um controle **Todos**.
- ⌨️ Atalhos globais que funcionam em qualquer programa, mesmo com a janela fechada.
- 🎚️ Presets com um valor para todos os monitores ou um valor diferente para cada um.
- ⚙️ Tela de configurações: crie e edite presets (nome, brilho de cada monitor e atalho) e grave atalhos pressionando as teclas.
- 🔔 Ícone na bandeja: o menu tem presets e opções, e a dica mostra o brilho atual de cada monitor.
- 🚀 Inicia minimizado com o Windows; dá para ligar ou desligar isso no menu, e não precisa de administrador.
- 📝 Tudo é salvo em `config.json`, que também pode ser editado à mão.
- 💻 Linha de comando: `--preset Noite`, `--definir 30 80`, `--listar`.

## Download e instalação

1. Baixe o **`ControleBrilho.zip`** da [**página de Releases**](https://github.com/jpnedes/controle-de-brilho/releases/latest).
2. Clique com o botão direito no arquivo → **Extrair Tudo…** → extraia em `C:\Programas`. Também funciona em `C:\Program Files`.
3. **Ative o DDC/CI no menu (OSD) de cada monitor.** Em muitos modelos ele vem desligado de fábrica.
4. Dê dois cliques em `C:\Programas\ControleBrilho\ControleBrilho.exe`.

O Windows SmartScreen pode mostrar o aviso *"O Windows protegeu o computador"*, porque o executável não é assinado digitalmente. Clique em **Mais informações → Executar assim mesmo**.

Na primeira execução, o programa cria suas configurações em `%APPDATA%\ControleBrilho\config.json` e passa a iniciar com o Windows, já minimizado na bandeja.

**O ícone ficou escondido?** Clique na seta **^** ao lado do relógio e arraste o ☀️ para a barra de tarefas.

## Atalhos padrão

Todos podem ser alterados em **⚙ Configurações…**.

| Atalho          | Ação                          |
|-----------------|-------------------------------|
| `Ctrl+Alt+1`    | Noite: 10%                    |
| `Ctrl+Alt+2`    | Médio: 50%                    |
| `Ctrl+Alt+3`    | Dia: 100%                     |
| `Ctrl+Alt+4`    | Leitura: 40% / 60%            |
| `Ctrl+Alt+PgUp` | +10% em todos os monitores    |
| `Ctrl+Alt+PgDn` | −10% em todos os monitores    |

## Bandeja do sistema

- **Clique esquerdo** no ☀️ abre os controles deslizantes.
- **Clique direito** abre o menu: presets, aumentar ou diminuir, **Configurações…**, **Iniciar com o Windows** e **Sair**.
- Fechar a janela no **X** só a esconde; os atalhos continuam ativos. Para encerrar de verdade, use **Sair**.
- Abrir o `.exe` de novo com o programa já rodando apenas mostra a janela, sem criar uma segunda cópia.

## Configurações

Clique em **⚙ Configurações…** na janela ou no menu da bandeja. As mudanças valem assim que você clica em **Salvar**, sem reiniciar o programa.

<p>
  <img src="docs/configuracoes.png" alt="Tela de configurações com monitores, lista de presets e atalhos de ajuste" height="420">
  &nbsp;&nbsp;
  <img src="docs/preset.png" alt="Edição de um preset: nome, brilho de cada monitor e atalho" height="190">
</p>

- **Monitores**: dê um nome a cada monitor; ele aparece na janela e nos presets.
- **Presets**: use **Adicionar**, **Editar** (ou dê duplo clique), **Remover** e as setas ▲ ▼ para mudar a ordem. Em cada preset você define:
  - o **nome**;
  - o **brilho de cada monitor**; marque **não alterar** para manter o brilho atual daquele monitor;
  - o **atalho**: clique no campo e pressione a combinação, por exemplo `Ctrl+Alt+5`. **Esc** cancela e **Limpar** remove o atalho.
- **Atalhos de ajuste**: as combinações para aumentar e diminuir o brilho e o **passo** em %.
- **Iniciar com o Windows**: liga ou desliga o início automático.

Os atalhos precisam usar **Ctrl**, **Alt** ou **Win** junto com outra tecla; as teclas F1–F24 também podem ser usadas sozinhas. Se o mesmo atalho estiver em dois lugares, o programa avisa antes de salvar.

### Editando o `config.json` à mão (opcional)

As configurações ficam em `%APPDATA%\ControleBrilho\config.json`. Para chegar até lá, use o botão **Abrir pasta das configurações** na tela de Configurações. Se editar o arquivo diretamente, use **Sair** no menu da bandeja e abra o programa de novo.

**Modo portátil:** se existir um `config.json` na mesma pasta do `.exe` e essa pasta permitir gravação (por exemplo, num pendrive), o programa usa esse arquivo.

```json
{
  "iniciar_com_windows": true,
  "passo": 10,
  "nomes_monitores": ["Esquerda", "Direita"],
  "atalhos": {
    "aumentar": "<ctrl>+<alt>+<page_up>",
    "diminuir": "<ctrl>+<alt>+<page_down>"
  },
  "presets": [
    {"nome": "Noite",   "atalho": "<ctrl>+<alt>+1", "brilho": 10},
    {"nome": "Leitura", "atalho": "<ctrl>+<alt>+4", "brilho": [40, 60]},
    {"nome": "Cinema",  "atalho": "<ctrl>+<alt>+9", "brilho": [18, null]}
  ]
}
```

| Opção                 | Descrição |
|-----------------------|-----------|
| `iniciar_com_windows` | `true` ou `false`. |
| `passo`               | Quanto os atalhos de aumentar e diminuir mudam, em %. |
| `nomes_monitores`     | Nomes exibidos na janela, na ordem de `--listar`. |
| `brilho`              | Um **número** vale para todos os monitores. Uma **lista** define um valor por monitor; `null` significa "não alterar". |
| `atalho`              | Formato do [pynput](https://pynput.readthedocs.io/en/latest/keyboard.html#global-hotkeys): `<ctrl>`, `<alt>`, `<shift>`, `<cmd>` (a tecla Windows), `<f1>`…`<f12>`, `<page_up>`, letras e números. |

## Linha de comando

```bash
ControleBrilho.exe                  # bandeja + janela + atalhos
ControleBrilho.exe --minimizado     # só a bandeja (é assim que inicia com o Windows)
ControleBrilho.exe --preset Noite   # aplica um preset e sai
ControleBrilho.exe --definir 50     # 50% em todos os monitores
ControleBrilho.exe --definir 30 80  # 30% no monitor 1, 80% no monitor 2
ControleBrilho.exe --listar         # mostra os monitores e o brilho atual
ControleBrilho.exe --simular        # testa a interface sem mexer nos monitores
```

## Problemas comuns

- **"Nenhum monitor com DDC/CI encontrado"**: o DDC/CI está desligado no OSD do monitor, ou o cabo, dock ou adaptador não repassa os comandos. Docks USB-C e adaptadores DisplayLink costumam bloquear; a conexão direta por **HDMI** ou **DisplayPort** é a mais confiável.
- **Só um monitor muda**: o outro provavelmente está com o DDC/CI desligado.
- **Um atalho não funciona**: outro programa pode estar usando a mesma combinação. Troque-a em **⚙ Configurações…**.
- **Erro "Permission denied … config.json"** (versão 1.1.0 ou anterior): acontecia com o programa em `C:\Program Files`. Atualize para a versão mais recente.
- **A tela do notebook não aparece**: é esperado, porque telas internas não usam DDC/CI.
- **Erro logo ao ligar o PC**: alguns monitores demoram a responder após o boot. Ao iniciar com o Windows, o programa tenta de novo por cerca de 1 minuto antes de avisar.

## Desinstalar

Clique com o botão direito no ☀️, desmarque **Iniciar com o Windows** e clique em **Sair**. Depois apague a pasta do programa e, se quiser remover também as configurações, a pasta `%APPDATA%\ControleBrilho`.

## Rodar pelo código-fonte

Requer Python 3.10 ou mais recente.

```bash
pip install -r requirements.txt
python brilho.py
```

Para gerar o `.exe`, execute o **`build.bat`** no Windows. O arquivo é criado em `dist\ControleBrilho.exe`.

### Publicar uma nova versão

Altere o número no arquivo [`VERSION`](VERSION), por exemplo para `1.1.0`, e envie para a `main`. O GitHub Actions compila o `.exe` no Windows e publica a release `v1.1.0` com o `ControleBrilho.zip`. Pushes que não mudam a versão não geram release nova.

O código também funciona no **Linux** (X11). Nesse caso:
- carregue o módulo `i2c-dev` (`sudo modprobe i2c-dev`) e dê ao seu usuário acesso a `/dev/i2c-*` (grupo `i2c`);
- o ícone na bandeja requer GTK/AppIndicator;
- no Wayland, os atalhos globais não funcionam; crie atalhos no próprio ambiente gráfico chamando `python3 brilho.py --preset Noite`.

## Tecnologias

[monitorcontrol](https://github.com/newAM/monitorcontrol) (DDC/CI) · [pynput](https://github.com/moses-palmer/pynput) (atalhos globais) · [pystray](https://github.com/moses-palmer/pystray) (bandeja) · tkinter (interface) · [PyInstaller](https://pyinstaller.org) (executável)
