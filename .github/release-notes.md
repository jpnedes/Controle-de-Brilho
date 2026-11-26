**Controle de Brilho** para Windows 64 bits. Não precisa instalar Python.

### Correção
- Corrige o erro **"Permission denied: 'C:\Program Files\config.json'"** ao abrir o programa instalado em `C:\Program Files` ou em outra pasta protegida.
- As configurações agora ficam em `%APPDATA%\ControleBrilho\config.json`, o que funciona em qualquer pasta, sem administrador. Se você já tinha um `config.json` ao lado do `.exe`, ele continua sendo usado.
- Novo botão **Abrir pasta das configurações** na tela de Configurações.

### Instalação
1. Baixe o **`ControleBrilho.zip`** abaixo e extraia, por exemplo, em `C:\Programas` ou `C:\Program Files`. Se já tiver uma versão anterior aberta, use **Sair** no menu da bandeja antes de substituir o `.exe`.
2. Ative o **DDC/CI** no menu (OSD) de cada monitor.
3. Abra `ControleBrilho.exe`. O programa vai para a bandeja (☀️) e passa a iniciar com o Windows.

Se aparecer o aviso do SmartScreen, clique em **Mais informações → Executar assim mesmo**. O executável não é assinado digitalmente.

Veja o [README](https://github.com/jpnedes/controle-de-brilho#readme) para mais detalhes.
