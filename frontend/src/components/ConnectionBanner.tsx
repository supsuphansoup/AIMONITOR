import React from 'react';
import { AlertTriangle, KeyRound } from 'lucide-react';

interface ConnectionBannerProps {
  error: { message: string; status?: number } | null;
  onOpenSettings: () => void;
}

/** Failures used to go to the console only, which made a dead backend look
 *  identical to an idle one. */
export const ConnectionBanner: React.FC<ConnectionBannerProps> = ({ error, onOpenSettings }) => {
  if (!error) return null;

  // Keyed off the status code rather than the message: when reads are gated the
  // dashboard cannot even load /system/info to find out that auth is on.
  const needsKey = error.status === 401 || error.status === 403;

  return (
    <div className="banner banner-error" role="alert">
      {needsKey ? <KeyRound size={16} /> : <AlertTriangle size={16} />}
      <span>{error.message}</span>
      {needsKey && (
        <button className="btn btn-secondary banner-action" onClick={onOpenSettings}>
          API 키 입력
        </button>
      )}
    </div>
  );
};
