'use client';

import { useActionState, useState } from 'react';
import { Button } from '@smartgym/ui';
import { updateNotificationPreferences } from '@/lib/actions';
import type { ActionResult } from '@/lib/actions';

interface Prefs {
  workout_reports: boolean;
  subscription_expiry_alerts: boolean;
  promotional_offers: boolean;
}

export function NotificationPrefsForm({ initialPrefs }: { initialPrefs: Prefs }) {
  const [workoutReports, setWorkoutReports] = useState(initialPrefs.workout_reports);
  const [expiryAlerts, setExpiryAlerts] = useState(initialPrefs.subscription_expiry_alerts);
  const [promotions, setPromotions] = useState(initialPrefs.promotional_offers);
  const [statusMessage, setStatusMessage] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    setSaving(true);
    setStatusMessage(null);
    try {
      const res = await updateNotificationPreferences({
        workout_reports: workoutReports,
        subscription_expiry_alerts: expiryAlerts,
        promotional_offers: promotions,
      });
      if (res.ok) {
        setStatusMessage('Preferences updated successfully.');
      } else {
        setStatusMessage(res.error ?? 'Failed to update preferences.');
      }
    } catch {
      setStatusMessage('Network error updating preferences.');
    } finally {
      setSaving(false);
    }
  };

  return (
    <form onSubmit={handleSave} className="space-y-4 pt-2">
      <div className="space-y-3">
        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={workoutReports}
            onChange={(e) => setWorkoutReports(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
          />
          <div>
            <span className="font-medium text-sm text-foreground block">Workout Summaries</span>
            <span className="text-xs text-muted-foreground block">
              Receive automated repetition, set counts, and technique breakdowns when leaving the gym.
            </span>
          </div>
        </label>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={expiryAlerts}
            onChange={(e) => setExpiryAlerts(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
          />
          <div>
            <span className="font-medium text-sm text-foreground block">Membership Expiry Reminders</span>
            <span className="text-xs text-muted-foreground block">
              Advance reminders 7 days and 3 days before your membership or freeze period expires.
            </span>
          </div>
        </label>

        <label className="flex items-start gap-3 cursor-pointer">
          <input
            type="checkbox"
            checked={promotions}
            onChange={(e) => setPromotions(e.target.checked)}
            className="mt-0.5 h-4 w-4 rounded border-border text-primary focus:ring-primary"
          />
          <div>
            <span className="font-medium text-sm text-foreground block">Special Offers &amp; Discounts</span>
            <span className="text-xs text-muted-foreground block">
              Exclusive branch discounts, renewal bonus days, and referral campaigns.
            </span>
          </div>
        </label>
      </div>

      {statusMessage && (
        <p className="text-xs font-medium text-primary">{statusMessage}</p>
      )}

      <Button type="submit" size="sm" disabled={saving}>
        {saving ? 'Saving…' : 'Save Preferences'}
      </Button>
    </form>
  );
}

