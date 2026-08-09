/**
 * Pause an <audio>/<video> element when the page is backgrounded.
 *
 * Chrome on Android auto-enters Picture-in-Picture for a playing <video>
 * when the user leaves the page (switches app, hits Home) - confirmed via a
 * screen recording where a story's rendered video kept narrating in a
 * floating PiP window on the home screen. visibilitychange fires before
 * that PiP window would otherwise keep the element playing, and it also
 * covers plain tab-switching / backgrounding on browsers without
 * Android-style auto-PiP (desktop, iOS Safari).
 */
import { useEffect } from 'react';

export function usePauseMediaOnHidden(mediaRef) {
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.hidden) mediaRef.current?.pause();
    };
    document.addEventListener('visibilitychange', onVisibilityChange);
    return () => document.removeEventListener('visibilitychange', onVisibilityChange);
  }, [mediaRef]);
}
