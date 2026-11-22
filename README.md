# mosAIc

<img src="./project-logo.png" alt="Logo de mosAIc" width="200" />

mosAIc detecta y notifica mensajes potencialmente reveladores. Evalúa texto escrito por personas o generado por agentes de IA y considera qué podría deducir el destinatario al combinar el mensaje con lo que ya sabe. La alerta queda disponible para revisión humana. El mensaje conserva su redacción original.

## Cómo funciona

1. El operador incorpora fuentes internas y configura qué información está protegida y quién puede recibirla.
2. Cuando se evalúa un mensaje, mosAIc extrae hechos con Claude y estima qué inferencias podría hacer ese destinatario. ProbLog calcula cuánto cambia el riesgo al sumar el mensaje a su historial.
3. Si la evaluación detecta un riesgo de divulgación, mosAIc registra una alerta en **Notificaciones**. Un operador puede abrir la evidencia y marcar la alerta como revisada o descartada.

Por ejemplo, «El banco está en Avenida de Ejemplo 123» puede ser información pública. Si el destinatario ya recibió ese mensaje, «Una persona vive al lado del banco» puede revelar la ubicación aproximada de su domicilio. La alerta corresponde al mensaje que completa la inferencia.

## Probarlo

Para usar el panel con Claude en Python 3.11 o posterior:

```bash
python -m pip install -e '.[api]'
mosaic key
python -m uvicorn isde.api:create_app --factory --host 127.0.0.1 --port 8001
```

Abre [el panel local](http://127.0.0.1:8001/). En **Simulación**, selecciona el destinatario y evalúa un mensaje de texto libre. Para probar una inferencia acumulativa, confirma la entrega del primer mensaje y luego evalúa el segundo para el mismo destinatario. Revisa el resultado en **Notificaciones**.

Puedes configurar `ANTHROPIC_API_KEY` en el entorno en lugar de usar `mosaic key`. Para abrir el detalle de las alertas, inicia la API con `MOSAIC_OPERATOR_TOKEN` y usa ese valor en **Acceso de operador**. La configuración, el historial y las alertas se guardan en SQLite. [Guía del panel](docs/panel.md)

### Demo sin credenciales

```bash
python -m pip install .
python -m isde demo
```

La demo de catálogo usa datos ficticios y corre en memoria. Para ejecutar la suite completa, instala el extra `api` y ejecuta `python -m unittest discover -s tests -v`. Las pruebas automatizadas usan Claude simulado. [Guion de la demo](docs/demo.md)

## Documentación

- Política YAML, historial, API y límites conocidos. [Documentación técnica](docs/isde.md)
- Configuración, simulación y revisión de alertas. [Guía del panel](docs/panel.md)
- Reglas y pruebas de inferencias encadenadas. [Inferencias multinivel](docs/inferencias-multinivel.md)
- Intranet ficticia en Node que usa la API de mosAIc. Cárgala con `git submodule update --init`. [AlphaCorp Intranet](https://github.com/jpherrerap/alphacorp-intranet)

## Equipo

Equipo 24 de Platanus Hack 26, Santiago ft. Buk. Track AI Security.

- Claudio Alexis Canales Pavez [@claudiocanalespavez](https://github.com/claudiocanalespavez)
- Gabriel Joaquín Carmona Díaz [@m1tuw](https://github.com/m1tuw)
- Juan Pablo Herrera Pizarro [@jpherrerap](https://github.com/jpherrerap)
- Diego Agustín Moreno Guzmán [@diegomorenoxor](https://github.com/diegomorenoxor)
- Sebastián Nicolás Ferrá Sepúlveda [@sebastianferra](https://github.com/sebastianferra)
