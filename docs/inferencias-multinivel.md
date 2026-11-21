# Pruebas de inferencia multinivel

La política de ejemplo formaliza `a ⇒ b ⇒ c ⇒ d`. Solo `d` es una familia confidencial. Las afirmaciones intermedias `b` y `c` pertenecen al vocabulario y pueden usarse como premisas de reglas posteriores.

## Probar el caso por la vía normal

```bash
mosaic --policy examples/multinivel-policy.yaml --case examples/multinivel-case.yaml --db multinivel.sqlite load examples/multinivel-history.yaml
mosaic --policy examples/multinivel-policy.yaml --case examples/multinivel-case.yaml --db multinivel.sqlite preview ana show_a --at 2026-01-01 --draft a
```

La respuesta debe ser `review`. La traza identifica la familia `d` y muestra que la conclusión se vuelve deducible después del borrador. La vista previa no entrega el mensaje.

## Reglas

Cada átomo contiene sujeto, predicado y objeto. Los nombres que empiezan por `?` son variables. Los predicados deben estar declarados en `vocabulary` o `families`. Las variables de la conclusión deben aparecer en alguna premisa. Cita `"yes"` en YAML para que se interprete como texto.

```yaml
rules:
  - when: [["?x", a, "yes"]]
    then: ["?x", b, "yes"]
  - when: [["?x", b, "yes"], ["?x", permiso, "yes"]]
    then: ["?x", c, "yes"]
  - when: [["?x", c, "yes"]]
    then: ["?x", d, "yes"]
```

Las premisas de una regla se combinan con Y. Se pueden declarar varias reglas con la misma conclusión para representar caminos alternativos. Las reglas son positivas y no crean individuos nuevos. Un hecho ausente queda desconocido. Para modelar una negación explícita, usa una afirmación distinta, por ejemplo `no`.

Las cadenas históricas de `chains` siguen siendo válidas y pueden combinarse con `rules`. OWL RL expande las relaciones del esquema. Las reglas positivas adicionales cierran el grafo interno, y ProbLog las usa junto con la evidencia del destinatario. Las derivaciones no se registran como fuentes independientes. Una misma pista utilizada por dos caminos conserva su dependencia.

## Ejecutar las pruebas

```bash
python -m unittest discover -s tests -p 'test_multinivel*.py' -v
```

La suite carga las políticas por el validador público y prueba inferencias de hasta ocho pasos, varias premisas, sujetos distintos, ciclos, caminos alternativos, probabilidades y destinatarios con distintos historiales. También verifica la CLI y la API semántica. Los datos son artificiales.

## Prueba opcional con Claude

Estas dos pruebas requieren una API key de Anthropic en `ANTHROPIC_API_KEY` y hacen dos llamadas reales con texto artificial.

```bash
MOSAIC_LIVE_CLAUDE=1 python -m unittest discover -s tests -p test_multinivel_claude.py -v
```

Una prueba presenta `a ⇒ b ⇒ c ⇒ d` y una afirmación de `a`. La otra omite `b ⇒ c`. Se evalúa si Claude propone la hipótesis `d` con una estimación de al menos 0,5 solo en el primer caso. Ese umbral es un criterio de prueba y la salida del modelo puede variar entre ejecuciones.
