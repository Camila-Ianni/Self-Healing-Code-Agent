# Self-Healing Code Agent

> 🎥 **Video demo (< 3 min):** `PEGAR AQUÍ EL ENLACE PÚBLICO DE YOUTUBE ANTES DE ENTREGAR`
>
> 🆔 **Codex Session ID (/feedback):** `019f824e-0a6a-7530-a6a1-4134a4093fd1`

CLI de Python que convierte un test fallido en un ciclo de reparación verificable: ejecuta los tests, captura el stack trace, localiza el archivo fuente, solicita una corrección a OpenAI y **solo conserva el cambio si los tests pasan**. Si el intento falla, restaura el archivo original.

**Cómo aceleró Codex el proyecto:** Codex generó el esqueleto de la CLI, separó el motor de reparación de la interfaz, creó la demo y ayudó a implementar el ciclo de validación con rollback. Así pude enfocar el tiempo en el flujo agentic y en una demo que un juez puede ejecutar de inmediato.

## Demo en 30 segundos

El ejemplo contiene a propósito un error en `example/calculator.py`: `multiply(6, 7)` suma en vez de multiplicar.

```bash
git clone https://github.com/Camila-Ianni/Self-Healing-Code-Agent.git
cd Self-Healing-Code-Agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
export OPENAI_API_KEY="tu_clave"

# Comando recomendado para jueces (no necesitan conocer opciones):
python -m self_healing_agent.cli test ./example/test_calculator.py
```

Salida esperada:

```text
❌ Tests fallaron. Analizando example/calculator.py con gpt-5.6…
✅ Reparación validada. Parche aplicado en example/calculator.py (...)
```

Luego `pytest -q example` queda en verde. Para repetir la demo, vuelve a cambiar `return left * right` por `return left + right`.

## Cómo funciona

```text
test command → stack trace → Fixer Agent → Reviewer Agent → diff + confirmación → tests
                                                         ├─ rechazo: no escribir nada
                                                         └─ aprobado: parche temporal → pasan: conservar / fallan: rollback
```

La arquitectura sigue una separación estricta Modelo–Controlador–Vista:

- `model.py`: evidencia inmutable del test y parseo profundo de stack traces Python/Go.
- `controller.py`: orquesta Fixer, Reviewer, aprobación humana y persistencia.
- `view.py`: spinner Rich, diff coloreado y confirmación en terminal.
- `sandbox.py`: copia temporal + contenedor Docker restringido para la validación.

El **Fixer Agent** propone el archivo completo corregido. Antes de cualquier escritura, el **Reviewer Agent** analiza esa propuesta buscando riesgos de seguridad, loops infinitos, regresiones de rendimiento, data races y deadlocks. Solo un veredicto `APPROVE` habilita el diff coloreado y la confirmación del usuario. Los backups se guardan en `.self-healing-backups/` y no se versionan.

Usá `--yes` para aceptar el parche aprobado sin interacción, útil para automatizaciones.

## Demo backend concurrente (Go)

Además de la calculadora, `example/go_backend/` reproduce una condición de carrera típica de un contador de requests de servidor. El test usa el detector de carreras de Go:

```bash
self-heal \
  --test-command "cd example/go_backend && go test -race ./..." \
  --source example/go_backend/counter.go
```

El Fixer debe reemplazar el acceso concurrente inseguro por sincronización correcta; el Reviewer revisa explícitamente race conditions y deadlocks antes de mostrar el diff. Esta demo requiere Go 1.22+ y `OPENAI_API_KEY`.

### Integración asíncrona de mercados

`example/go_market_aggregator/` simula respuestas JSON de plataformas de mercados predictivos. El bug bloquea goroutines contra un channel sin buffer mientras el `sync.WaitGroup` espera que terminen; el test expone un timeout con el diagnóstico exacto.

```bash
self-heal \
  --test-command "cd example/go_market_aggregator && go test ./..." \
  --source example/go_market_aggregator/aggregator.go
```

La reparación esperada coordina correctamente `channels` y `sync.WaitGroup`, y conserva la deserialización JSON de los feeds concurrentes.

## Sandbox de validación

Antes de sobrescribir un archivo local, la propuesta aprobada se copia a un directorio temporal y se prueba dentro de un contenedor Docker efímero. Ese contenedor ejecuta con red deshabilitada, sin capacidades Linux, `no-new-privileges`, límite de 768 MB, dos CPUs y hasta 256 procesos. El único volumen montado es la copia temporal, nunca tu working tree.

Construí la imagen una sola vez:

```bash
docker build -t self-healing-sandbox:latest -f Dockerfile.sandbox .
```

Sin esa imagen, la CLI rechaza el parche y no escribe nada. Podés proveer otra imagen compatible con `--sandbox-image nombre:tag`.

## Configuración

La CLI utiliza la [Responses API de OpenAI](https://developers.openai.com/api/docs/guides/text). Por defecto usa `gpt-5.6`; se puede ajustar sin editar código:

```bash
export OPENAI_MODEL="gpt-5.6"
self-heal --test-command "pytest -q"
```

Opciones principales:

```text
--test-command "pytest -q"       Comando a ejecutar y capturar
--source path/to/module.py        Archivo que el agente puede modificar
--model gpt-5.6                   Modelo de OpenAI
--root .                          Raíz del proyecto
```

## Verificación del proyecto

```bash
pip install -e '.[dev]'
pytest -q
```

Los tests propios validan la captura de fallos y la localización segura del archivo. La reparación end-to-end necesita una `OPENAI_API_KEY` válida, por lo que no se ejecuta durante la suite local.

## Seguridad y límites

- El modelo nunca recibe permisos de shell: devuelve solamente el contenido completo del archivo seleccionado.
- Dos llamadas independientes separan la propuesta (Fixer) de la aprobación de seguridad (Reviewer).
- El diff se muestra y requiere confirmación antes de escribir; `--yes` es la única excepción explícita.
- El código propuesto se ejecuta solo en el sandbox Docker antes de persistirlo localmente.
- El archivo solo queda modificado tras una nueva ejecución exitosa de los tests.
- La versión actual está enfocada en Python y en reparar un archivo por ciclo; no sustituye revisión humana para cambios de producción.

## Uso de Codex y GPT-5.6

Codex aceleró la creación del esqueleto de la CLI, la suite de pruebas, el flujo de rollback y esta documentación. GPT-5.6 es el motor de razonamiento dentro del producto: recibe el código y la evidencia concreta del test fallido, propone una corrección mínima y el agente la valida de forma automática.

## Video

Pegá el enlace público de YouTube al comienzo de este README antes de entregar.
