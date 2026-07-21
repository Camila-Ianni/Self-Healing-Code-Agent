# Self-Healing Code Agent

CLI de Python que convierte un test fallido en un ciclo de reparación verificable: ejecuta los tests, captura el stack trace, localiza el archivo fuente, solicita una corrección a OpenAI y **solo conserva el cambio si los tests pasan**. Si el intento falla, restaura el archivo original.

## Demo en 30 segundos

El ejemplo contiene a propósito un error en `example/calculator.py`: `multiply(6, 7)` suma en vez de multiplicar.

```bash
git clone https://github.com/Camila-Ianni/Self-Healing-Code-Agent.git
cd Self-Healing-Code-Agent
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e '.[dev]'
export OPENAI_API_KEY="tu_clave"

# Desde la raíz del repositorio:
self-heal --test-command "pytest -q example" --source example/calculator.py
```

Salida esperada:

```text
❌ Tests fallaron. Analizando example/calculator.py con gpt-5.6…
✅ Reparación validada. Parche aplicado en example/calculator.py (...)
```

Luego `pytest -q example` queda en verde. Para repetir la demo, vuelve a cambiar `return left * right` por `return left + right`.

## Cómo funciona

```text
test command → stack trace → archivo fuente → GPT-5.6 → parche temporal → tests
                                                                  ├─ pasan: conservar
                                                                  └─ fallan: rollback
```

El comando acepta `--source` para una reparación determinista. Sin ese argumento, intenta inferir un archivo Python local y que no sea un test desde el traceback. Los backups se guardan en `.self-healing-backups/` y no se versionan.

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
- El archivo solo queda modificado tras una nueva ejecución exitosa de los tests.
- La versión actual está enfocada en Python y en reparar un archivo por ciclo; no sustituye revisión humana para cambios de producción.

## Uso de Codex y GPT-5.6

Codex aceleró la creación del esqueleto de la CLI, la suite de pruebas, el flujo de rollback y esta documentación. GPT-5.6 es el motor de razonamiento dentro del producto: recibe el código y la evidencia concreta del test fallido, propone una corrección mínima y el agente la valida de forma automática.

**Codex Session ID:** `PENDIENTE — ejecutá /feedback en Codex y pegá aquí el Session ID antes de la entrega.`

## Video

Pendiente de agregar el enlace público de YouTube (menos de 3 minutos) antes de la entrega.
