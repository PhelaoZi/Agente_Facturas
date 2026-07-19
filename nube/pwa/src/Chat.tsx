import React, { useState, useRef, useEffect } from 'react';
import { Send, Trash2 } from 'lucide-react';
import { enviarMensajeChat } from './api';

interface Mensaje {
  rol: 'usuario' | 'asistente';
  texto: string;
}

const CLAVE_SESION = 'zigurat_chat_sesion';
const CLAVE_MENSAJES = 'zigurat_chat_mensajes';

// Pestaña Chat: conversa con el analista de negocio (edge function /chat).
// La sesion y los mensajes persisten en localStorage para sobrevivir cierres
// de la PWA; "limpiar" parte una conversacion nueva (el servidor conserva la
// antigua en chat_sesiones como respaldo).
export default function Chat() {
  const [mensajes, setMensajes] = useState<Mensaje[]>(() => {
    try {
      return JSON.parse(localStorage.getItem(CLAVE_MENSAJES) || '[]');
    } catch {
      return [];
    }
  });
  const [borrador, setBorrador] = useState('');
  const [pensando, setPensando] = useState(false);
  const [error, setError] = useState('');
  const finRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    localStorage.setItem(CLAVE_MENSAJES, JSON.stringify(mensajes));
    finRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [mensajes, pensando]);

  const enviar = async () => {
    const texto = borrador.trim();
    if (!texto || pensando) return;
    setBorrador('');
    setError('');
    setMensajes((prev) => [...prev, { rol: 'usuario', texto }]);
    setPensando(true);
    try {
      const sesionGuardada = localStorage.getItem(CLAVE_SESION);
      const r = await enviarMensajeChat(texto, sesionGuardada ? Number(sesionGuardada) : null);
      localStorage.setItem(CLAVE_SESION, String(r.sesion_id));
      setMensajes((prev) => [...prev, { rol: 'asistente', texto: r.respuesta }]);
    } catch (err: any) {
      setError(err?.message || 'Error de conexión con el chat.');
    } finally {
      setPensando(false);
    }
  };

  const limpiar = () => {
    if (!window.confirm('¿Empezar una conversación nueva?')) return;
    localStorage.removeItem(CLAVE_SESION);
    localStorage.removeItem(CLAVE_MENSAJES);
    setMensajes([]);
    setError('');
  };

  const alTeclear = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      enviar();
    }
  };

  return (
    <section id="view-chat" className="chat-container">
      <div className="chat-mensajes">
        {mensajes.length === 0 && !pensando && (
          <div className="chat-vacio">
            <p>Pregúntame por el negocio:</p>
            <p>"¿Cuánto hemos vendido?" · "¿Cómo va el flujo de caja?" · "Top de clientes"</p>
          </div>
        )}
        {mensajes.map((m, i) => (
          <div key={i} className={`chat-burbuja ${m.rol}`}>
            {m.texto}
          </div>
        ))}
        {pensando && <div className="chat-burbuja asistente chat-pensando">Consultando…</div>}
        {error && <div className="error-banner">{error}</div>}
        <div ref={finRef} />
      </div>

      <div className="chat-input-bar">
        <button
          id="btn-chat-limpiar"
          className="chat-btn-limpiar"
          onClick={limpiar}
          title="Conversación nueva"
        >
          <Trash2 size={18} />
        </button>
        <textarea
          id="chat-input"
          className="chat-input"
          rows={1}
          placeholder="Escribe tu consulta…"
          value={borrador}
          onChange={(e) => setBorrador(e.target.value)}
          onKeyDown={alTeclear}
          disabled={pensando}
        />
        <button
          id="btn-chat-enviar"
          className="chat-btn-enviar"
          onClick={enviar}
          disabled={pensando || !borrador.trim()}
        >
          <Send size={18} />
        </button>
      </div>
    </section>
  );
}
