import { createContext, useState, useContext, useCallback, useRef } from 'react';
import { AnimatePresence } from 'framer-motion';
import ConfirmDialog from '../components/ConfirmDialog';

const DialogContext = createContext();

// Same shape react-dom rejects two overlapping calls, first paint wins:
// resolveRef only ever holds the promise that's currently on screen, so a
// second confirm()/alert() fired before the first resolves would silently
// drop the first caller's promise. None of today's call sites can trigger
// that (each is behind a user action that closes the dialog first), but
// it's the reason this doesn't queue - if that changes, queue here instead
// of resolving out from under a caller.
export const DialogProvider = ({ children }) => {
  const [dialog, setDialog] = useState(null);
  const resolveRef = useRef(null);

  const closeDialog = useCallback((result) => {
    resolveRef.current?.(result);
    resolveRef.current = null;
    setDialog(null);
  }, []);

  // Resolves true/false - the caller decides what to do next, same as
  // `if (window.confirm(...))` did.
  const confirm = useCallback((message, options = {}) => {
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      setDialog({ ...options, mode: 'confirm', message });
    });
  }, []);

  // Resolves once dismissed - purely for "wait until the user has seen
  // this", matching window.alert()'s blocking-until-OK behavior.
  const alertDialog = useCallback((message, options = {}) => {
    return new Promise((resolve) => {
      resolveRef.current = resolve;
      setDialog({ ...options, mode: 'alert', message });
    });
  }, []);

  return (
    <DialogContext.Provider value={{ confirm, alert: alertDialog }}>
      {children}
      <AnimatePresence>
        {dialog && (
          <ConfirmDialog
            {...dialog}
            onConfirm={() => closeDialog(true)}
            onCancel={() => closeDialog(false)}
          />
        )}
      </AnimatePresence>
    </DialogContext.Provider>
  );
};

// Custom hook for easy access to the dialog context
export const useDialog = () => {
  const context = useContext(DialogContext);
  if (context === undefined) {
    throw new Error('useDialog must be used within a DialogProvider');
  }
  return context;
};
