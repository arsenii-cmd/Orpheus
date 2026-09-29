package dev.arco.orpheus

import android.content.Context
import android.media.AudioDeviceCallback
import android.media.AudioDeviceInfo
import android.media.AudioManager
import android.os.Build
import android.os.Handler
import android.os.Looper

/**
 * The earbuds' microphone (Galaxy Buds and the like) for everything Orpheus hears, while they are
 * connected: the owner talks into them, and the phone may lie on a desk a metre away (28.09: read
 * from there, a voice stood only 4-7 dB over the room and no phrase was ever noticed).
 *
 * Android records from the phone's own microphone unless an app asks for the headset's: here the
 * buds become the communication device (Bluetooth LE Audio, or the classic headset profile) and the
 * recording prefers their input. Connected or gone later, it follows; with the setting off, or
 * without buds, it is the phone's microphone as before. While it holds the buds' microphone the
 * link is a headset call's: music in the buds sounds like a call, and their battery goes faster.
 */
class HeadsetMic(private val context: Context, private val onInput: (AudioDeviceInfo?) -> Unit) {
    private val audio = context.getSystemService(AudioManager::class.java)
    private val main = Handler(Looper.getMainLooper())
    private var wanted = false
    private var started = false
    /** The buds while they are the communication device, else null. */
    var device: AudioDeviceInfo? = null
        private set

    private val callback = object : AudioDeviceCallback() {
        override fun onAudioDevicesAdded(added: Array<out AudioDeviceInfo>) = update()
        override fun onAudioDevicesRemoved(removed: Array<out AudioDeviceInfo>) = update()
    }
    // a call, or another app, may take the communication device or clear it: then the buds are not ours,
    // and after the call they are asked for again
    private val deviceChanged = AudioManager.OnCommunicationDeviceChangedListener { update() }
    private val modeChanged = AudioManager.OnModeChangedListener { update() }

    fun start() {
        if (started) return
        started = true
        audio.registerAudioDeviceCallback(callback, main)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audio.addOnCommunicationDeviceChangedListener(context.mainExecutor, deviceChanged)
            audio.addOnModeChangedListener(context.mainExecutor, modeChanged)
        }
        update()
    }

    fun setWanted(on: Boolean) {
        wanted = on
        if (started) update()
    }

    fun stop() {
        if (!started) return
        started = false
        audio.unregisterAudioDeviceCallback(callback)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            audio.removeOnCommunicationDeviceChangedListener(deviceChanged)
            audio.removeOnModeChangedListener(modeChanged)
        }
        release()
    }

    private fun update() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.S) return  // setCommunicationDevice: Android 12
        if (device != null && audio.communicationDevice?.id != device?.id) {
            // taken from us (a call, another app): not ours any more; what the recording hears says the rest
            device = null
            android.util.Log.i("Orpheus", "mic: headset taken by another use")
            onInput(null)
        }
        val free = audio.mode == AudioManager.MODE_NORMAL  // never in the middle of a call
        val buds = if (wanted && started && free) audio.availableCommunicationDevices.firstOrNull { it.type in BUDS } else null
        if (buds != null && buds.id == device?.id) return
        if (buds == null) {
            release()
            return
        }
        if (audio.setCommunicationDevice(buds)) {
            device = buds
            android.util.Log.i("Orpheus", "mic: headset (${typeName(buds.type)})")
            onInput(inputOf(buds))
        } else {
            release()
        }
    }

    private fun release() {
        if (device == null) return
        device = null
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) audio.clearCommunicationDevice()
        android.util.Log.i("Orpheus", "mic: phone")
        onInput(null)
    }

    /** The buds' microphone: the input of the same device as the output that became the communication device
     *  (a car kit may be connected too, of the same kind). */
    private fun inputOf(output: AudioDeviceInfo): AudioDeviceInfo? {
        val inputs = audio.getDevices(AudioManager.GET_DEVICES_INPUTS).filter { it.type == output.type }
        return inputs.firstOrNull { it.address.isNotEmpty() && it.address == output.address } ?: inputs.singleOrNull()
    }

    companion object {
        val BUDS = setOf(AudioDeviceInfo.TYPE_BLE_HEADSET, AudioDeviceInfo.TYPE_BLUETOOTH_SCO)
        private fun typeName(type: Int) = if (type == AudioDeviceInfo.TYPE_BLE_HEADSET) "LE Audio" else "SCO"
    }
}
