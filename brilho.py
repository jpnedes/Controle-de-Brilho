#!/usr/bin/env python3
"""
Controle de Brilho — ajusta o brilho de monitores externos via DDC/CI
e oferece atalhos de teclado globais para níveis pré-definidos.

Uso:
    python brilho.py                  # ícone na bandeja + janela + atalhos
    python brilho.py --minimizado     # só o ícone na bandeja (usado ao iniciar o Windows)
    python brilho.py --sem-janela     # só os atalhos, sem janela nem bandeja
    python brilho.py --listar         # lista monitores e brilho atual
    python brilho.py --definir 50     # 50% em todos os monitores
    python brilho.py --definir 40 70  # 40% no monitor 1, 70% no monitor 2
    python brilho.py --preset Noite   # aplica um preset do config.json
    python brilho.py --simular        # testa sem monitores reais
"""

import argparse
import json
import queue
import socket
import sys
import threading
import time
from pathlib import Path

# Empacotado como .exe (PyInstaller), o config.json fica ao lado do executável.
if getattr(sys, "frozen", False):
    PASTA = Path(sys.executable).resolve().parent
else:
    PASTA = Path(__file__).resolve().parent
ARQUIVO_CONFIG = PASTA / "config.json"
TEM_CONSOLE = sys.stdout is not None and not getattr(sys, "frozen", False)


def avisar(mensagem, erro=False):
    """Mostra no terminal ou, no .exe sem console, numa caixa de diálogo."""
    if TEM_CONSOLE:
        print(mensagem, file=sys.stderr if erro else sys.stdout)
        return
    try:
        import tkinter as tk
        from tkinter import messagebox
        r = tk.Tk()
        r.withdraw()
        (messagebox.showerror if erro else messagebox.showinfo)("Controle de Brilho", mensagem)
        r.destroy()
    except Exception:
        pass


def falhar(mensagem):
    avisar(mensagem, erro=True)
    sys.exit(1)

CONFIG_PADRAO = {
    "iniciar_com_windows": True,
    "passo": 10,
    "nomes_monitores": ["Esquerda", "Direita"],
    "atalhos": {
        "aumentar": "<ctrl>+<alt>+<page_up>",
        "diminuir": "<ctrl>+<alt>+<page_down>",
    },
    "presets": [
        {"nome": "Noite", "atalho": "<ctrl>+<alt>+1", "brilho": 10},
        {"nome": "Médio", "atalho": "<ctrl>+<alt>+2", "brilho": 50},
        {"nome": "Dia", "atalho": "<ctrl>+<alt>+3", "brilho": 100},
        {"nome": "Leitura", "atalho": "<ctrl>+<alt>+4", "brilho": [40, 60]},
    ],
}


# --------------------------------------------------------------------------
# Configuração
# --------------------------------------------------------------------------

def carregar_config():
    if not ARQUIVO_CONFIG.exists():
        ARQUIVO_CONFIG.write_text(
            json.dumps(CONFIG_PADRAO, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"Arquivo de configuração criado em {ARQUIVO_CONFIG}")
    try:
        cfg = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        falhar(f"Erro no {ARQUIVO_CONFIG.name}: {e}")
    for chave, valor in CONFIG_PADRAO.items():
        cfg.setdefault(chave, valor)
    return cfg


def salvar_opcao(chave, valor):
    """Altera uma única opção no config.json, preservando o resto do arquivo."""
    try:
        dados = json.loads(ARQUIVO_CONFIG.read_text(encoding="utf-8"))
        dados[chave] = valor
        ARQUIVO_CONFIG.write_text(json.dumps(dados, indent=2, ensure_ascii=False), encoding="utf-8")
    except Exception as e:
        avisar(f"Não foi possível salvar o config.json: {e}", erro=True)


# --------------------------------------------------------------------------
# Monitores
# --------------------------------------------------------------------------

class MonitorDDC:
    """Monitor físico controlado via DDC/CI (biblioteca monitorcontrol)."""

    def __init__(self, monitor, nome):
        self._mon = monitor
        self.nome = nome

    def ler(self):
        with self._mon:
            return int(self._mon.get_luminance())

    def definir(self, valor):
        with self._mon:
            self._mon.set_luminance(int(valor))


class MonitorSimulado:
    """Monitor falso, para testar a interface e os atalhos."""

    def __init__(self, nome, valor=70):
        self.nome = nome
        self._valor = valor

    def ler(self):
        return self._valor

    def definir(self, valor):
        time.sleep(0.05)  # simula a latência do DDC/CI
        self._valor = int(valor)
        print(f"[simulado] {self.nome} -> {valor}%")


def detectar_monitores(cfg, simular=False, tentativas=1):
    nomes = cfg.get("nomes_monitores") or []

    def nome(i):
        return nomes[i] if i < len(nomes) else f"Monitor {i + 1}"

    if simular:
        return [MonitorSimulado(nome(0), 70), MonitorSimulado(nome(1), 40)]

    try:
        from monitorcontrol import get_monitors
    except ImportError:
        falhar("Instale as dependências: pip install -r requirements.txt")

    erro = None
    for tentativa in range(max(1, tentativas)):
        if tentativa:
            time.sleep(5)
        try:
            lista = get_monitors()
        except Exception as e:
            erro = e
            continue
        encontrados = []
        for mon in lista:
            candidato = MonitorDDC(mon, nome(len(encontrados)))
            try:
                candidato.ler()  # telas sem DDC/CI (ex.: a do notebook) falham aqui
            except Exception:
                continue
            encontrados.append(candidato)
        if encontrados:
            return encontrados

    if erro:
        falhar(
            "Não foi possível consultar os monitores via DDC/CI.\n\n"
            f"Detalhe: {erro}\n\n"
            "Verifique se o DDC/CI está ativado no menu (OSD) de cada monitor "
            "e se o driver da placa de vídeo está atualizado."
        )
    falhar(
        "Nenhum monitor com DDC/CI encontrado.\n"
        "- Verifique se o DDC/CI está ativado no menu (OSD) de cada monitor.\n"
        "- No Linux: 'sudo modprobe i2c-dev' e acesso a /dev/i2c-* (veja o README)."
    )


# --------------------------------------------------------------------------
# Controlador: guarda o brilho desejado e aplica em uma thread separada
# --------------------------------------------------------------------------

class Controlador:
    """
    DDC/CI é lento (dezenas a centenas de ms por comando). Para a interface
    não travar, os pedidos vão para uma fila; se chegarem vários pedidos para
    o mesmo monitor antes de serem aplicados, só o último é enviado.
    """

    def __init__(self, monitores, passo=10):
        self.monitores = monitores
        self.passo = passo
        self.valores = []
        for m in monitores:
            try:
                self.valores.append(m.ler())
            except Exception:
                self.valores.append(50)
        self._pendentes = {}
        self._cond = threading.Condition()
        threading.Thread(target=self._trabalhador, daemon=True).start()

    def definir(self, indice, valor):
        valor = max(0, min(100, int(round(valor))))
        with self._cond:
            self.valores[indice] = valor
            self._pendentes[indice] = valor
            self._cond.notify()

    def aplicar(self, brilho):
        """brilho = número (todos os monitores) ou lista (um valor por monitor)."""
        if isinstance(brilho, (int, float)):
            brilho = [brilho] * len(self.monitores)
        for i, valor in enumerate(brilho[: len(self.monitores)]):
            if valor is not None:
                self.definir(i, valor)

    def ajustar(self, sinal):
        for i, atual in enumerate(list(self.valores)):
            self.definir(i, atual + sinal * self.passo)

    def _trabalhador(self):
        while True:
            with self._cond:
                while not self._pendentes:
                    self._cond.wait()
                indice, valor = self._pendentes.popitem()
            try:
                self.monitores[indice].definir(valor)
            except Exception as e:
                print(f"Falha ao ajustar {self.monitores[indice].nome}: {e}")


# --------------------------------------------------------------------------
# Atalhos globais
# --------------------------------------------------------------------------

def iniciar_atalhos(cfg, ctrl):
    try:
        from pynput import keyboard
    except Exception as e:
        avisar(f"Atalhos globais indisponíveis: {e}", erro=True)
        return None

    mapa = {}

    def adicionar(atalho, acao, descricao):
        if not atalho:
            return
        try:
            keyboard.HotKey.parse(atalho)
        except ValueError:
            avisar(f"Atalho inválido ignorado: {atalho!r} ({descricao})", erro=True)
            return
        if atalho in mapa:
            avisar(f"Atalho repetido ignorado: {atalho!r} ({descricao})", erro=True)
            return
        mapa[atalho] = acao

    atalhos = cfg.get("atalhos", {})
    adicionar(atalhos.get("aumentar"), lambda: ctrl.ajustar(+1), "aumentar")
    adicionar(atalhos.get("diminuir"), lambda: ctrl.ajustar(-1), "diminuir")
    for p in cfg.get("presets", []):
        adicionar(p.get("atalho"), lambda b=p["brilho"]: ctrl.aplicar(b), p.get("nome"))

    if not mapa:
        return None

    # No Windows, com Ctrl/Alt pressionados a tecla "1" chega só como código
    # virtual (vk=49), sem o caractere — e o GlobalHotKeys do pynput não a
    # reconhece. Por isso comparamos letras e números pelo código virtual.
    def normalizar(tecla):
        if not isinstance(tecla, keyboard.KeyCode) or sys.platform != "win32":
            return tecla
        if tecla.vk is not None:
            return keyboard.KeyCode.from_vk(tecla.vk)
        if tecla.char and len(tecla.char) == 1 and tecla.char.isascii() and tecla.char.isalnum():
            return keyboard.KeyCode.from_vk(ord(tecla.char.upper()))
        return tecla

    combinacoes = [
        keyboard.HotKey([normalizar(t) for t in keyboard.HotKey.parse(atalho)], acao)
        for atalho, acao in mapa.items()
    ]

    def ao_pressionar(tecla):
        tecla = normalizar(ouvinte.canonical(tecla))
        for c in combinacoes:
            c.press(tecla)

    def ao_soltar(tecla):
        tecla = normalizar(ouvinte.canonical(tecla))
        for c in combinacoes:
            c.release(tecla)

    ouvinte = keyboard.Listener(on_press=ao_pressionar, on_release=ao_soltar)
    ouvinte.daemon = True
    ouvinte.start()
    return ouvinte


def descrever_atalho(atalho):
    if not atalho:
        return ""
    partes = atalho.replace("<", "").replace(">", "").split("+")
    nomes = {"ctrl": "Ctrl", "alt": "Alt", "shift": "Shift", "cmd": "Win",
             "page_up": "PgUp", "page_down": "PgDn", "up": "↑", "down": "↓"}
    return "+".join(nomes.get(p, p.upper()) for p in partes)




# --------------------------------------------------------------------------
# Iniciar com o Windows (registro HKCU\...\Run — não precisa de administrador)
# --------------------------------------------------------------------------

CHAVE_RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"
NOME_RUN = "ControleBrilho"


def comando_autostart():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimizado'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpretador = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpretador}" "{Path(__file__).resolve()}" --minimizado'


def autostart_ativo():
    if sys.platform != "win32":
        return False
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN) as k:
            valor, _ = winreg.QueryValueEx(k, NOME_RUN)
            return bool(valor)
    except OSError:
        return False


def definir_autostart(ativo):
    if sys.platform != "win32":
        return
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, CHAVE_RUN, 0, winreg.KEY_SET_VALUE) as k:
            if ativo:
                winreg.SetValueEx(k, NOME_RUN, 0, winreg.REG_SZ, comando_autostart())
            else:
                try:
                    winreg.DeleteValue(k, NOME_RUN)
                except FileNotFoundError:
                    pass
    except OSError as e:
        avisar(f"Não foi possível alterar o início automático: {e}", erro=True)


# --------------------------------------------------------------------------
# Instância única: se já estiver rodando, a 2ª cópia só pede para mostrar a janela
# --------------------------------------------------------------------------

PORTA_INSTANCIA = 47813


def reservar_instancia(pedidos, mostrar):
    """Retorna True se esta é a única instância; senão avisa a outra e retorna False."""
    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    if sys.platform == "win32":
        servidor.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
    try:
        servidor.bind(("127.0.0.1", PORTA_INSTANCIA))
    except OSError:
        servidor.close()
        if mostrar:
            try:
                with socket.create_connection(("127.0.0.1", PORTA_INSTANCIA), timeout=2) as c:
                    c.sendall(b"mostrar")
            except OSError:
                pass
        return False

    servidor.listen(2)

    def atender():
        while True:
            try:
                conexao, _ = servidor.accept()
                with conexao:
                    if conexao.recv(32).startswith(b"mostrar"):
                        pedidos.put("mostrar")
            except OSError:
                return

    threading.Thread(target=atender, daemon=True).start()
    return True


# --------------------------------------------------------------------------
# Ícone na bandeja do sistema (pystray)
# --------------------------------------------------------------------------

def imagem_icone():
    from PIL import Image, ImageDraw
    arquivo = Path(getattr(sys, "_MEIPASS", PASTA)) / "icone.ico"
    if arquivo.exists():
        return Image.open(arquivo)
    import math
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    cor = (255, 184, 28, 255)
    for k in range(8):
        a = k * math.pi / 4
        d.line([(32 + 21 * math.cos(a), 32 + 21 * math.sin(a)),
                (32 + 30 * math.cos(a), 32 + 30 * math.sin(a))], fill=cor, width=5)
    d.ellipse([17, 17, 47, 47], fill=cor)
    return img


def criar_bandeja(cfg, ctrl, pedidos):
    """Cria o ícone da bandeja. Ações de janela vão para a fila `pedidos`,
    que a thread do tkinter processa (tkinter não é seguro entre threads)."""
    try:
        import pystray
    except Exception as e:
        avisar(f"Ícone na bandeja indisponível: {e}", erro=True)
        return None

    def item_preset(p):
        texto = p["nome"]
        if p.get("atalho"):
            texto += f"\t{descrever_atalho(p['atalho'])}"
        return pystray.MenuItem(texto, lambda: ctrl.aplicar(p["brilho"]))

    def alternar_autostart():
        novo = not autostart_ativo()
        definir_autostart(novo)
        salvar_opcao("iniciar_com_windows", novo)

    itens = [
        pystray.MenuItem("Abrir controles", lambda: pedidos.put("mostrar"), default=True),
        pystray.Menu.SEPARATOR,
        *[item_preset(p) for p in cfg.get("presets", [])],
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(f"Aumentar {ctrl.passo}%", lambda: ctrl.ajustar(+1)),
        pystray.MenuItem(f"Diminuir {ctrl.passo}%", lambda: ctrl.ajustar(-1)),
        pystray.Menu.SEPARATOR,
    ]
    if sys.platform == "win32":
        itens.append(pystray.MenuItem("Iniciar com o Windows", alternar_autostart,
                                      checked=lambda item: autostart_ativo()))
    itens.append(pystray.MenuItem("Sair", lambda: pedidos.put("sair")))

    icone = pystray.Icon("ControleBrilho", imagem_icone(), "Controle de Brilho",
                         pystray.Menu(*itens))
    try:
        icone.run_detached()
    except Exception as e:
        avisar(f"Ícone na bandeja indisponível: {e}", erro=True)
        return None
    return icone


def texto_dica(ctrl):
    partes = [f"{m.nome}: {v}%" for m, v in zip(ctrl.monitores, ctrl.valores)]
    return ("Brilho — " + " · ".join(partes))[:127]  # limite do Windows


# --------------------------------------------------------------------------
# Janela (tkinter)
# --------------------------------------------------------------------------

def executar_interface(cfg, ctrl, pedidos, minimizado):
    import tkinter as tk
    from tkinter import ttk

    raiz = tk.Tk()
    raiz.title("Controle de Brilho")
    raiz.resizable(False, False)
    icone_ico = Path(getattr(sys, "_MEIPASS", PASTA)) / "icone.ico"
    if icone_ico.exists() and sys.platform == "win32":
        raiz.iconbitmap(str(icone_ico))

    bandeja = criar_bandeja(cfg, ctrl, pedidos)
    if minimizado and bandeja:
        raiz.withdraw()

    def mostrar():
        raiz.deiconify()
        raiz.lift()
        raiz.attributes("-topmost", True)
        raiz.after(200, lambda: raiz.attributes("-topmost", False))
        raiz.focus_force()

    def sair():
        if bandeja:
            bandeja.stop()
        raiz.destroy()

    # Fechar a janela só a esconde; o programa continua na bandeja.
    raiz.protocol("WM_DELETE_WINDOW", raiz.withdraw if bandeja else sair)

    quadro = ttk.Frame(raiz, padding=14)
    quadro.grid()
    barras = []

    def ao_mover(indice):
        def callback(valor):
            valor = int(float(valor))
            if valor != ctrl.valores[indice]:  # ignora quando foi a sincronização
                ctrl.definir(indice, valor)
        return callback

    linha = 0
    for i, mon in enumerate(ctrl.monitores):
        ttk.Label(quadro, text=mon.nome, width=12).grid(row=linha, column=0, sticky="w")
        barra = tk.Scale(quadro, from_=0, to=100, orient="horizontal", length=280,
                         resolution=1, command=ao_mover(i))
        barra.set(ctrl.valores[i])
        barra.grid(row=linha, column=1, pady=2)
        barras.append(barra)
        linha += 1

    barra_todos = None
    estado_todos = {"ultimo": None}
    if len(ctrl.monitores) > 1:
        ttk.Separator(quadro).grid(row=linha, column=0, columnspan=2, sticky="ew", pady=6)
        linha += 1
        ttk.Label(quadro, text="Todos", width=12).grid(row=linha, column=0, sticky="w")

        def mover_todos(valor):
            valor = int(float(valor))
            if valor != estado_todos["ultimo"]:
                estado_todos["ultimo"] = valor
                ctrl.aplicar(valor)

        barra_todos = tk.Scale(quadro, from_=0, to=100, orient="horizontal",
                               length=280, resolution=1, command=mover_todos)
        barra_todos.grid(row=linha, column=1, pady=2)
        linha += 1

    botoes = ttk.Frame(quadro)
    botoes.grid(row=linha, column=0, columnspan=2, pady=(10, 0))
    for p in cfg.get("presets", []):
        texto = p["nome"]
        if p.get("atalho"):
            texto += f"\n{descrever_atalho(p['atalho'])}"
        ttk.Button(botoes, text=texto,
                   command=lambda b=p["brilho"]: ctrl.aplicar(b)).pack(side="left", padx=3)
    linha += 1

    atalhos = cfg.get("atalhos", {})
    dica = (f"{descrever_atalho(atalhos.get('aumentar'))}: +{ctrl.passo}%   "
            f"{descrever_atalho(atalhos.get('diminuir'))}: −{ctrl.passo}%")
    ttk.Label(quadro, text=dica, foreground="gray").grid(
        row=linha, column=0, columnspan=2, pady=(10, 0))

    # Atalhos e bandeja rodam em outras threads; aqui a janela acompanha as
    # mudanças e executa os pedidos de mostrar/sair.
    ultimo = {"valores": None}

    def sincronizar():
        while True:
            try:
                pedido = pedidos.get_nowait()
            except queue.Empty:
                break
            if pedido == "mostrar":
                mostrar()
            elif pedido == "sair":
                sair()
                return

        valores = list(ctrl.valores)
        if valores != ultimo["valores"]:
            ultimo["valores"] = valores
            for barra, v in zip(barras, valores):
                if int(barra.get()) != v:
                    barra.set(v)
            if barra_todos is not None:
                media = round(sum(valores) / len(valores))
                if media != estado_todos["ultimo"]:
                    estado_todos["ultimo"] = media
                    barra_todos.set(media)
            if bandeja:
                bandeja.title = texto_dica(ctrl)
        raiz.after(150, sincronizar)

    sincronizar()
    raiz.mainloop()


# --------------------------------------------------------------------------
# Entrada
# --------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description="Controle de brilho de monitores externos (DDC/CI).")
    ap.add_argument("--listar", action="store_true", help="lista monitores e brilho atual")
    ap.add_argument("--definir", nargs="+", type=int, metavar="N",
                    help="define o brilho (um valor para todos ou um por monitor)")
    ap.add_argument("--preset", metavar="NOME", help="aplica um preset do config.json")
    ap.add_argument("--minimizado", action="store_true",
                    help="inicia só com o ícone na bandeja, sem abrir a janela")
    ap.add_argument("--sem-janela", action="store_true",
                    help="roda só os atalhos globais, sem janela nem bandeja")
    ap.add_argument("--simular", action="store_true", help="usa monitores falsos (teste)")
    args = ap.parse_args()

    cfg = carregar_config()
    comando_direto = args.listar or args.definir or args.preset

    pedidos = queue.Queue()
    if not comando_direto and not reservar_instancia(pedidos, mostrar=not args.minimizado):
        return  # já está rodando; a outra instância mostra a janela

    # Ao iniciar com o Windows, os monitores podem demorar a responder.
    monitores = detectar_monitores(cfg, args.simular, tentativas=12 if args.minimizado else 1)

    # Comandos diretos: aplicam e saem (úteis para atalhos do próprio sistema)
    if args.listar:
        avisar("\n".join(f"{i}. {m.nome}: {m.ler()}%" for i, m in enumerate(monitores, 1)))
        return

    if args.definir or args.preset:
        if args.preset:
            preset = next((p for p in cfg["presets"]
                           if p["nome"].lower() == args.preset.lower()), None)
            if not preset:
                nomes = ", ".join(p["nome"] for p in cfg["presets"])
                falhar(f"Preset '{args.preset}' não existe. Disponíveis: {nomes}")
            brilho = preset["brilho"]
        else:
            brilho = args.definir[0] if len(args.definir) == 1 else args.definir
        if isinstance(brilho, (int, float)):
            brilho = [brilho] * len(monitores)
        for m, valor in zip(monitores, brilho):
            if valor is not None:
                m.definir(max(0, min(100, int(valor))))
        return

    # Mantém o registro do início automático em dia (inclusive se o .exe mudar de pasta).
    if sys.platform == "win32" and not args.simular:
        definir_autostart(bool(cfg.get("iniciar_com_windows", True)))

    ctrl = Controlador(monitores, cfg.get("passo", 10))
    ouvinte = iniciar_atalhos(cfg, ctrl)

    if args.sem_janela:
        if not ouvinte:
            falhar("Nenhum atalho ativo; nada a fazer.")
        print("Atalhos ativos. Ctrl+C para sair.")
        try:
            ouvinte.join()
        except KeyboardInterrupt:
            pass
        return

    executar_interface(cfg, ctrl, pedidos, args.minimizado)


if __name__ == "__main__":
    main()
