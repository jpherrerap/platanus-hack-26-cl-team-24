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

Una familia puede usar `any_value: true` en lugar de `statuses`: así protege cualquier valor representable que aparezca o se derive en el grafo. Esto no enumera todos los valores posibles ni garantiza la extracción desde texto libre. Las familias con `statuses` siguen limitadas a los valores declarados.

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

## Prompt de la vista adversarial

El prompt general está en `isde/prompts.py` y se usa al interpretar una tarjeta externa, proponer hechos de una fuente externa y evaluar un borrador. Pide explorar combinaciones, reglas encadenadas, interpretaciones alternativas y conocimiento adicional posible desde la vista de cada destinatario. El grafo interno y las vistas de otros destinatarios no se envían como contexto.

La salida actual son hipótesis con un triple del vocabulario de la política, una cita literal y `p_if_seen`: la estimación de que el destinatario alcance esa interpretación si ve el texto. No es la probabilidad de verdad ni una estimación calibrada. El sistema valida el esquema, los predicados y la cita; esto no garantiza que una inferencia del modelo sea correcta o completa. Las hipótesis adversariales no agregan reglas nuevas a la política.

## Construcción progresiva desde fuentes

El prompt interno en `isde/prompts.py` pide hechos explícitos y propone reglas expresadas en la fuente. Cada regla contiene `when`, `then`, una cita literal y una confianza de extracción. El validador exige variables ligadas, predicados existentes, estados válidos, identificadores seguros y una cita que aparezca en la fuente. No interpreta código Prolog entregado por el modelo. Estas verificaciones no prueban que la regla capture correctamente el significado de la cita.

Las propuestas se muestran antes de aceptarlas, junto con su fuente, fecha, cita, confianza y modelo. Una fuente aceptada registra todo el lote en una transacción. Para evaluar el riesgo, mosAIc considera conservadoramente que el destinatario **podría** conocer las reglas propuestas, aun cuando no tenga una evidencia específica de conocerlas. Las añade al razonamiento formal con peso 1; la confianza del modelo queda registrada como metadato y **no** se multiplica por la probabilidad de conocimiento. El cálculo de ProbLog mantiene las probabilidades de acceso a las pistas, sujetas a esta suposición conservadora.

Las consecuencias de reglas propuestas aparecen en `possible_inferences` de la vista interna; no se consideran hechos confirmados para verificar una respuesta. La vista adversarial del modelo no recibe las reglas propuestas internamente ni el grafo privado; el motor sí las usa para calcular el riesgo. Incorporar reglas cambia el contexto de cualquier revisión previa, que debe evaluarse de nuevo antes de la entrega.

Para ensayar con Claude y el caso artificial, instala el extra `claude`, configura `ANTHROPIC_API_KEY` y ejecuta:

```bash
mosaic --policy examples/multinivel-dinamico-policy.yaml --case examples/multinivel-case.yaml --db dinamico.sqlite load examples/multinivel-history.yaml
mosaic --policy examples/multinivel-dinamico-policy.yaml --case examples/multinivel-case.yaml --db dinamico.sqlite propose examples/multinivel-dinamico-source.yaml > propuestas.json
cat propuestas.json
mosaic --policy examples/multinivel-dinamico-policy.yaml --case examples/multinivel-case.yaml --db dinamico.sqlite load propuestas.json
mosaic --policy examples/multinivel-dinamico-policy.yaml --case examples/multinivel-case.yaml --db dinamico.sqlite preview ana show_a --at 2026-01-01 --draft a
```

Si Claude identifica las tres reglas en la fuente, el último comando deja `a` en revisión porque conduce a `d`. La extracción puede variar: revisa `propuestas.json` antes de cargarlo. La API `/v1/sources` también permite obtener propuestas con `accept: false` e incorporarlas con `accept: true`; cada llamada analiza nuevamente la fuente. El vocabulario y las familias deben existir en la política; todavía no se incorporan predicados ni categorías sensibles nuevas automáticamente. La detección de conocimientos sin relación con el texto analizado y la explicación completa de cada cadena siguen pendientes.

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
