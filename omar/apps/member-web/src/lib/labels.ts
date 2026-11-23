import type { ConsentType } from '@smartgym/supabase-client/types';

export const CONSENT_LABELS: Record<ConsentType, { title: string; description: string }> = {
  biometric_face_identification: {
    title: 'Face identification at the entrance',
    description: 'Lets the entrance camera recognise you so your session starts automatically. Optional: QR check-in works without it.',
  },
  cctv_workout_tracking: {
    title: 'Workout tracking on gym cameras',
    description: 'Lets the gym cameras follow your workout to count sets and reps. Required for automatic workout reports.',
  },
  data_processing: {
    title: 'Processing of membership data',
    description: 'Storing your membership, attendance and workout history in the member system.',
  },
  marketing: {
    title: 'Marketing messages',
    description: 'Occasional offers and news from your gym. Never required.',
  },
};

export const UNCERTAIN_REASON_LABELS: Record<string, string> = {
  occlusion: 'You were hidden from the cameras (someone or something blocked the view)',
  no_camera_coverage: 'No camera could see you',
  identity_ambiguous: 'The system could not tell you apart from another person',
  camera_offline: 'A camera was offline',
  low_tracking_confidence: 'Tracking confidence was too low',
};

export const CHECKIN_METHOD_LABELS: Record<string, string> = {
  face: 'Face',
  qr: 'QR code',
  nfc: 'NFC card',
  staff_manual: 'Front desk',
};
