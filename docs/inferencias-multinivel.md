# Pruebas de inferencia multinivel

Los escenarios usan símbolos inventados. La propiedad `a` pertenece a la entidad `x`. La relación `anchor` vincula `x` con `n`, y `self_link` vincula `n` consigo misma. Esas dos relaciones permiten expresar en el motor actual la cadena abstracta `a ⇒ b ⇒ c ⇒ d`. La política protege `d`. Los pasos `b` y `c` se declaran como familias, pero el rol de prueba está autorizado a conocerlos.

## Ejecutar

```bash
python -m unittest discover -s tests -p 'test_multinivel*.py' -v
```

La suite determinista cubre cadenas de dos, tres y cuatro inferencias, ausencia de una premisa, dos destinatarios, un ciclo sin causa inicial, probabilidades antes y después y dos caminos que comparten la misma pista. También prueba una divulgación anterior del propio `a`. Los datos y reglas del escenario son artificiales.

La política multinivel real se rechaza en el cargador. Para explorar el motor después de ese punto, el fixture crea una instancia experimental que omite deliberadamente esa validación. No se carga desde YAML ni representa un flujo que funcione normalmente en producción. En esta instancia, OWL deriva las conclusiones y ProbLog calcula la probabilidad de que el destinatario las reúna. También se comprueba que el motor actual falla si los pasos intermedios no son familias.

El resultado incluye un `expected failure`. Corresponde al requisito todavía pendiente de aceptar la política de tres niveles mediante el cargador público. Las pruebas que describen la limitación también deben modificarse cuando se implemente ese soporte.

## Prueba opcional con Claude

Estas dos pruebas requieren una API key de Anthropic en `ANTHROPIC_API_KEY` y hacen dos llamadas reales con texto artificial.

```bash
MOSAIC_LIVE_CLAUDE=1 python -m unittest discover -s tests -p test_multinivel_claude.py -v
```

La primera presenta al modelo las reglas `a ⇒ b ⇒ c ⇒ d` y una afirmación de `a`. La segunda omite `b ⇒ c`. Se comprueba si Claude propone la hipótesis `d` con una estimación de al menos 0,5 solo en el primer caso. Ese umbral es un criterio de prueba y la salida del modelo puede variar entre ejecuciones. Estas pruebas evalúan las hipótesis devueltas por Claude, con independencia de la ruta de carga de políticas del guardrail.
