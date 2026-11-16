import android.os.Looper;
import android.os.SystemClock;
import java.io.File;
import java.lang.reflect.Constructor;
import java.lang.reflect.Method;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.util.UUID;

/**
 * Samsung's speaker tuning for the guest (experiment, 0.4.58; started by the
 * UI activator unless ONEOS_GUEST_DOLBY=0).
 *
 * One UI's audioserver runs Samsung's processing on what it plays: Dolby DAX3
 * with Samsung's speaker endpoint tuning ("ProfileParamCache(There is specific
 * tuning for endpiont: speaker)", "setEndpointTuning(endpoint=speaker,
 * tuning=speaker)"), SoundBooster Plus and SoundAlive. The guest runs its own
 * audioserver (oneos_switch.sh, run 29), which only has the vendor effect HAL:
 * nothing is attached to its outputs ("0 Effect Chains" on every output thread,
 * report 4933a41d) and ColorOS's own engine (AudioEffectCenter's effect
 * 41f6c0f4-5d8f-11ec-bf63-0242ac130002) is not in Samsung's HAL. The vendor
 * effect HAL does list Dolby ("DAX3_S_3.14.0.0_r1", type 46d279d9): this
 * creates it on the output mix (session 0), enables it, activates Samsung's
 * Dolby DAX3 profile (profile = 0 Auto by default; without EFFECT_PARAM_PROFILE
 * Samsung's AHAL_DlbEffectContext stays on profile = -1, its "Dolby Atmos OFF"
 * bypass table where speaker EQ ceon=0, Audio Regulator aron=0 and Bass
 * Enhancer bexe=0), and holds it until the guest's system_server (args[0]) is
 * gone. args[1] may name another effect type (empty uses DOLBY_TYPE); args[2]
 * may override the Dolby profile index (0=Auto, 1=Movie, 2=Music, 3=Voice).
 *
 * Also bridges ColorOS's Mono Audio accessibility toggle (Settings.System
 * "master_mono") into AOSP's AudioSystem.setMasterMono(boolean): on ColorOS 17
 * AudioFlingerExtImpl sends "mono_output=1" to AudioFlinger.setParameters(),
 * which stock AOSP / Samsung HAL rejects ("1 KVP rejected: mono_output=1"),
 * leaving Master mono off unless AudioSystem.setMasterMono() is invoked.
 *
 * 0.4.65 (on by default; F8U InfinityX test): an output-mix effect is put on
 * the output music plays on when it is created, and stays there. Created at
 * boot it sat on the primary output while music (YouTube in a browser) played
 * on the deep-buffer one without it; created while music played it was on
 * that output. So it is created again each time music starts (STREAM_MUSIC
 * active after being idle, AudioSystem.isStreamActive, checked every 2 s), 2 s
 * after the start.
 *
 * Runs inside the guest through app_process, like OneOSCameraWorker. The
 * constructor taking a type and an implementation is hidden, hence reflection.
 */
public final class OneOSSoundFx {
 private static final String DOLBY_TYPE = "46d279d9-9be7-453d-9d7c-ef937f675587";
 private static final String TYPE_NULL = "ec7178ec-e5e1-4432-a3f4-4657e6795210";

 // Direct AHAL_DlbEffectContext parameter IDs on Samsung AIDL audio HAL:
 // AudioEffect.setParameter(int param, int value) / getParameter(int param, int[] value)
 // pass `param` directly as the 32-bit paramId into AHAL_DlbEffectContext::setParamImpl/getParamImpl.
 // (Previously passing EFFECT_PARAM_CPDP_VALUES + paramId sent invalid IDs 0x0A000005 / 0x03000005.)
 private static final int EFFECT_PARAM_ENABLE = 0x00000000;
 private static final int EFFECT_PARAM_CPDP_VALUES = 5;
 private static final int EFFECT_PARAM_PROFILE_COUNT = 0x03000000;
 private static final int EFFECT_PARAM_PROFILE = 0x0A000000;

 public static void main(String[] args) throws Exception {
  final int guest = Integer.parseInt(args[0]);
  final UUID type = UUID.fromString(args.length > 1 && args[1] != null && !args[1].isEmpty() ? args[1] : DOLBY_TYPE);
  final int profile = parseProfile(args.length > 2 ? args[2] : null);
  Looper.prepareMainLooper();

  // Initial sync of master_mono if the user already enabled Mono Audio in Settings.
  Boolean lastMono = syncMasterMono(null);

  Class<?> c = Class.forName("android.media.audiofx.AudioEffect");
  Constructor<?> k = c.getDeclaredConstructor(UUID.class, UUID.class, int.class, int.class);
  k.setAccessible(true);
  Object fx = null;
  // The guest's audioserver registers late (guest_start.sh): try for a minute.
  for (int i = 0; fx == null && i < 30 && alive(guest); i++) {
   fx = create(c, k, type, profile, i == 0 || i == 29 ? "try " + (i + 1) : null);
   if (fx == null) Thread.sleep(2000);
  }
  if (fx == null) {
   log("no effect of type " + type + " on the output mix; keeping master_mono bridge only");
  }
  // 0.5.11: music already playing when it was made counts as a start too (it was made at boot,
  // on whatever output ran then; S25 Ultra LineageOS 89eaa265, "speakers sound muffled": the
  // effect sat on the idle deep-buffer output for 10 minutes, 0 re-attachments).
  boolean was = false;
  int moves = 0;
  while (alive(guest)) {
   Thread.sleep(2000);
   lastMono = syncMasterMono(lastMono);
   if (fx == null) continue;
   boolean now = musicActive();
   if (now && !was) {
    // Music started: create it again, so it is on the output music plays on. Not at
    // once: created right at the start it went to the primary output again (F8U test);
    // created 2 s in, with the music's output running, it is on that output.
    Thread.sleep(2000);
    if (!alive(guest)) break;
    try { c.getMethod("release").invoke(fx); } catch (Throwable t) { }
    fx = null;
    Object f = create(c, k, type, profile, moves < 5 ? "music started" : null);
    if (f != null) fx = f;
    else { Thread.sleep(1000); fx = create(c, k, type, profile, "music started (again)"); }
    moves++;
   }
   was = now;
  }
  if (fx != null) c.getMethod("release").invoke(fx);
  log("guest system_server " + guest + " gone; released (" + moves + " re-attachments)");
 }

 static int parseProfile(String raw) {
  if (raw == null || raw.isEmpty()) return 0;
  try {
   int p = Integer.parseInt(raw.trim());
   return p >= 0 && p <= 9 ? p : 0;
  } catch (NumberFormatException e) {
   return 0;
  }
 }

 /** The effect on the output mix, enabled; null when it cannot be created. why: log it (null: quiet). */
 private static Object create(Class<?> c, Constructor<?> k, UUID type, int profile, String why) {
  try {
   Object fx = k.newInstance(type, UUID.fromString(TYPE_NULL), 0, 0);
   String dapInfo = DOLBY_TYPE.equalsIgnoreCase(type.toString()) ? configureDolby(c, fx, profile) : "";
   Object r = c.getMethod("setEnabled", boolean.class).invoke(fx, true);
   if (why != null) {
    Object d = c.getMethod("getDescriptor").invoke(fx);
    log("created " + field(d, "name") + " " + field(d, "uuid") + " (" + field(d, "implementor") + ") on the output mix ("
        + why + "); setEnabled(true) = " + r + ", enabled " + c.getMethod("getEnabled").invoke(fx)
        + ", has control " + c.getMethod("hasControl").invoke(fx) + dapInfo);
   }
   return fx;
  } catch (Throwable t) {
   Throwable e = t.getCause() != null ? t.getCause() : t;
   if (why != null) log("create " + type + " failed (" + why + "): " + e);
   return null;
  }
 }

 static String configureDolby(Class<?> c, Object fx, int profile) {
  StringBuilder sb = new StringBuilder();
  try {
   Method setInt = c.getMethod("setParameter", int.class, int.class);
   Method getInt = c.getMethod("getParameter", int.class, int[].class);

   Integer before = readDirectInt(getInt, fx, EFFECT_PARAM_PROFILE);
   Integer count = readDirectInt(getInt, fx, EFFECT_PARAM_PROFILE_COUNT);
   sb.append(", profileBefore=").append(before != null ? before : "?");
   if (count != null) sb.append("/").append(count);

   // 1. Direct AIDL parameter IDs (EFFECT_PARAM_ENABLE = 0, EFFECT_PARAM_PROFILE = 0x0A000000).
   int sEn = invokeSetInt(setInt, fx, EFFECT_PARAM_ENABLE, 1);
   int sPr = invokeSetInt(setInt, fx, EFFECT_PARAM_PROFILE, profile);
   Integer after = readDirectInt(getInt, fx, EFFECT_PARAM_PROFILE);
   sb.append(", direct(en=").append(sEn).append(",pr=").append(sPr)
     .append("->").append(after != null ? after : "?").append(")");

   // 2. Fallback to legacy CPDP packet (param=5, [paramId, 1, val]) only if direct profile readback
   // is still -1 (Dolby Atmos OFF bypass table) or unavailable.
   if (after == null || after == -1) {
    Method setBytes = c.getMethod("setParameter", int.class, byte[].class);
    int cEn = setCpdpInt(setBytes, fx, EFFECT_PARAM_ENABLE, 1);
    int cPr = setCpdpInt(setBytes, fx, EFFECT_PARAM_PROFILE, profile);
    Integer afterCpdp = readDirectInt(getInt, fx, EFFECT_PARAM_PROFILE);
    sb.append(", cpdp(en=").append(cEn).append(",pr=").append(cPr)
      .append("->").append(afterCpdp != null ? afterCpdp : "?").append(")");
   }

   // 3. Probe low-numbered parameter IDs (0..4) read-only on first creation if neither 0x0A000000
   // nor 0x03000000 answered, so sound_fx.log records the exact vendor enum layout without guessing.
   if (before == null && count == null) {
    StringBuilder probe = new StringBuilder();
    for (int id = 0; id <= 4; id++) {
     Integer v = readDirectInt(getInt, fx, id);
     if (v != null) {
      if (probe.length() > 0) probe.append(' ');
      probe.append(id).append('=').append(v);
     }
    }
    if (probe.length() > 0) sb.append(", probe[").append(probe).append(']');
   }
  } catch (Throwable t) {
   Throwable e = t.getCause() != null ? t.getCause() : t;
   sb.append(", dapConfigErr=").append(e);
  }
  return sb.toString();
 }

 private static int invokeSetInt(Method setInt, Object fx, int paramId, int value) {
  try {
   Object res = setInt.invoke(fx, paramId, value);
   return res instanceof Integer ? (Integer) res : -99;
  } catch (Throwable t) {
   return -98;
  }
 }

 private static Integer readDirectInt(Method getInt, Object fx, int paramId) {
  try {
   int[] val = new int[]{Integer.MIN_VALUE};
   Object res = getInt.invoke(fx, paramId, val);
   int n = res instanceof Integer ? (Integer) res : -1;
   // AudioEffect.getParameter(int, int[]) returns the number of bytes read (4 for int32) or SUCCESS (0) on some OEMs.
   if ((n >= 4 || n == 0) && val[0] != Integer.MIN_VALUE) {
    return val[0];
   }
  } catch (Throwable ignored) { }
  return null;
 }

 private static int setCpdpInt(Method setBytes, Object fx, int paramId, int value) {
  try {
   byte[] buf = ByteBuffer.allocate(12).order(ByteOrder.LITTLE_ENDIAN)
       .putInt(paramId).putInt(1).putInt(value).array();
   Object res = setBytes.invoke(fx, EFFECT_PARAM_CPDP_VALUES, buf);
   return res instanceof Integer ? (Integer) res : -99;
  } catch (Throwable t) {
   return -98;
  }
 }

 /**
  * Sync Settings.System "master_mono" -> AudioSystem.setMasterMono(boolean) whenever it changes
  * (or on startup when master_mono == 1), fixing ColorOS's rejected "mono_output=1" HAL parameter.
  */
 private static Boolean syncMasterMono(Boolean prev) {
  try {
   Integer setting = readSystemMasterMono();
   if (setting == null) return prev;
   boolean want = (setting == 1);
   if (prev == null) {
    if (!want) return Boolean.FALSE;
   } else if (prev.booleanValue() == want) {
    return prev;
   }
   Class<?> as = Class.forName("android.media.AudioSystem");
   Object r = as.getMethod("setMasterMono", boolean.class).invoke(null, want);
   Object cur = null;
   try { cur = as.getMethod("getMasterMono").invoke(null); } catch (Throwable ignored) { }
   log("master_mono=" + setting + " -> AudioSystem.setMasterMono(" + want + ")=" + r
       + (cur != null ? " (getMasterMono=" + cur + ")" : ""));
   return want;
  } catch (Throwable t) {
   return prev;
  }
 }

 private static Integer readSystemMasterMono() {
  try {
   Class<?> at = Class.forName("android.app.ActivityThread");
   Object app = at.getMethod("currentApplication").invoke(null);
   if (app == null) {
    Object sysMain = at.getMethod("systemMain").invoke(null);
    app = at.getMethod("getSystemContext").invoke(sysMain);
   }
   if (app == null) return null;
   Object cr = app.getClass().getMethod("getContentResolver").invoke(app);
   Class<?> sys = Class.forName("android.provider.Settings$System");
   Object v = sys.getMethod("getInt", Class.forName("android.content.ContentResolver"), String.class, int.class)
       .invoke(null, cr, "master_mono", -1);
   if (v instanceof Integer && ((Integer) v == 0 || (Integer) v == 1)) {
    return (Integer) v;
   }
  } catch (Throwable ignored) { }
  return null;
 }

 /** STREAM_MUSIC (3) active now (AudioSystem.isStreamActive, hidden); false when it cannot be asked. */
 private static boolean musicActive() {
  try {
   Class<?> s = Class.forName("android.media.AudioSystem");
   return (Boolean) s.getMethod("isStreamActive", int.class, int.class).invoke(null, 3, 0);
  } catch (Throwable t) {
   return false;
  }
 }

 private static boolean alive(int pid) { return new File("/proc/" + pid).exists(); }

 private static Object field(Object o, String n) {
  try { return o.getClass().getField(n).get(o); } catch (Throwable t) { return "?"; }
 }

 private static void log(String s) {
  System.out.println(SystemClock.elapsedRealtime() + " " + s);
  System.out.flush();
 }
}
