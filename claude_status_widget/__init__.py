"""Nucleo del widget de estado: logica pura, sin dependencias de interfaz.

Se separa de `widget/` a proposito: aqui no se importa tkinter, pystray ni
PIL, de modo que todo lo de este paquete es testeable sin entorno grafico.
"""
