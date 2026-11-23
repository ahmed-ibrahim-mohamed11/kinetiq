import { describe, expect, it } from 'vitest';
import {
  MemberCode,
  NonDegeneratePolygon,
  isValidTimeZone,
  normalizeConfidence,
  normalizePhone,
  parseQrCheckInToken,
  pointInPolygon,
  polygonArea,
} from '../src/index';

describe('geometry', () => {
  const square: Array<[number, number]> = [
    [0, 0],
    [10, 0],
    [10, 10],
    [0, 10],
  ];
  it('detects inside/outside points', () => {
    expect(pointInPolygon([5, 5], square)).toBe(true);
    expect(pointInPolygon([15, 5], square)).toBe(false);
    expect(pointInPolygon([-1, -1], square)).toBe(false);
  });
  it('computes area', () => {
    expect(polygonArea(square)).toBe(100);
  });
  it('rejects degenerate polygons', () => {
    expect(NonDegeneratePolygon.safeParse([[0, 0], [1, 0], [2, 0]]).success).toBe(false);
    expect(NonDegeneratePolygon.safeParse(square).success).toBe(true);
  });
});

describe('phone normalization (Egypt default)', () => {
  it('normalizes local mobile formats', () => {
    expect(normalizePhone('010 1234 5678')).toBe('+201012345678');
    expect(normalizePhone('201112345678')).toBe('+201112345678');
    expect(normalizePhone('+201512345678')).toBe('+201512345678');
  });
  it('rejects garbage', () => {
    expect(normalizePhone('12345')).toBeNull();
    expect(normalizePhone('abc')).toBeNull();
  });
});

describe('member code', () => {
  it('uppercases and rejects ambiguous characters', () => {
    expect(MemberCode.parse(' gym-2a7k ')).toBe('GYM-2A7K');
    expect(MemberCode.safeParse('GYM-0O1I').success).toBe(false);
  });
});

describe('misc', () => {
  it('validates timezones', () => {
    expect(isValidTimeZone('Africa/Cairo')).toBe(true);
    expect(isValidTimeZone('Mars/Olympus')).toBe(false);
  });
  it('normalizes confidence', () => {
    expect(normalizeConfidence(1.7)).toBe(1);
    expect(normalizeConfidence(-0.2)).toBe(0);
    expect(normalizeConfidence(0.123456)).toBe(0.1235);
    expect(normalizeConfidence(Number.NaN)).toBe(0);
  });
  it('parses QR token shape', () => {
    expect(parseQrCheckInToken('SGM1.GYM-2A7K.29876543.abcDEF123456')).toEqual({
      memberCode: 'GYM-2A7K',
      minute: 29876543,
      mac: 'abcDEF123456',
    });
    expect(parseQrCheckInToken('garbage')).toBeNull();
  });
});
