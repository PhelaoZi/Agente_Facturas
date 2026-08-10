# Informe diario de IA agéntica con audio

## Objetivo

Enviar cada mañana a Christian un correo con el resumen escrito de novedades sobre IA agéntica y un archivo MP3 complementario, en español, apto para escuchar mientras maneja.

## Alcance aprobado

- Un informe diario breve basado en fuentes primarias y enlaces verificables.
- Un guion oral distinto del texto del correo, de 350 a 420 palabras, para una duración aproximada de dos a tres minutos.
- Un MP3 generado con `gpt-4o-mini-tts` de OpenAI y adjunto al correo enviado desde la cuenta Gmail conectada.
- Voz integrada de OpenAI, tono claro y profesional, velocidad normal.
- Un envío inicial de prueba antes de activar el envío recurrente.

## Flujo

1. La tarea programada recopila y filtra novedades relevantes desde las fuentes definidas.
2. Produce el informe escrito con enlaces, impacto para Zigurat y una acción práctica.
3. Condensa el contenido en un guion oral, sin URLs leídas en voz alta y sin superar el límite del modelo TTS.
4. Genera el MP3 mediante la API de OpenAI.
5. Envía un solo correo a `cdelafue31@gmail.com` con el informe escrito y el MP3 adjunto.

## Seguridad y costos

- La clave se lee exclusivamente desde `OPENAI_API_KEY` en `.env`; nunca se imprime ni se agrega al repositorio.
- No se clona ni se usa una voz personalizada.
- Se limita el guion a menos de tres minutos para mantener un costo estimado de US$0,045 por día y US$1,35 al mes.
- Si falla la generación del audio, se envía el informe escrito y se deja constancia clara de la falla; no se reintentan envíos indefinidamente.

## Verificación

- Confirmar que el MP3 se genera correctamente y se adjunta al correo de prueba.
- Verificar que el audio se reproduzca, esté en español y no exceda tres minutos.
- Revisar que el correo conserve los enlaces y que la ausencia de audio no impida recibir el informe escrito.

## Fuera de alcance

- Cambios en datos del ERP, documentos tributarios o inventario.
- Envíos a otros destinatarios.
- Generación de voz en tiempo real o transcripción de audios.
