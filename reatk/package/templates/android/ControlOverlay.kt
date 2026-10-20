package @@NAMESPACE@@

import android.content.Context
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.view.MotionEvent
import android.view.View
import org.json.JSONObject

/**
 * The on-screen controls, drawn from the layout in `reatk/manifest.json`.
 *
 * Every rectangle in the layout is a fraction of the view, so one layout works
 * at any density. Touches inside the bottom-screen rectangle become stylus
 * positions in frame pixels; touches on a button set its bit in the pad mask.
 */
class ControlOverlay(context: Context, private val manifest: JSONObject) : View(context) {

    private data class Control(val id: String, val shape: String, val rect: RectF)

    private val fill = Paint().apply {
        color = Color.argb(40, 255, 255, 255)
        isAntiAlias = true
    }
    private val outline = Paint().apply {
        color = Color.argb(90, 255, 255, 255)
        style = Paint.Style.STROKE
        strokeWidth = 2f
        isAntiAlias = true
    }
    private val label = Paint().apply {
        color = Color.argb(160, 255, 255, 255)
        textSize = 28f
        textAlign = Paint.Align.CENTER
        isAntiAlias = true
    }

    private var controls: List<Control> = emptyList()
    private var bottomScreen: RectF? = null

    private fun layoutKey() = if (width > height) "landscape" else "portrait"

    private fun rebuild() {
        val orientation = manifest
            .optJSONObject("controls")
            ?.optJSONObject(layoutKey())
            ?: return

        controls = (0 until (orientation.optJSONArray("buttons")?.length() ?: 0)).mapNotNull {
            val spec = orientation.getJSONArray("buttons").getJSONObject(it)
            val values = spec.optJSONArray("rect") ?: return@mapNotNull null
            Control(
                spec.optString("id"),
                spec.optString("shape", "pill"),
                fractionsToPixels(values),
            )
        }
        bottomScreen = orientation.optJSONObject("screens")
            ?.optJSONArray("bottom")
            ?.let { fractionsToPixels(it) }
    }

    private fun fractionsToPixels(values: org.json.JSONArray): RectF {
        val x = values.getDouble(0).toFloat() * width
        val y = values.getDouble(1).toFloat() * height
        return RectF(
            x,
            y,
            x + values.getDouble(2).toFloat() * width,
            y + values.getDouble(3).toFloat() * height,
        )
    }

    override fun onSizeChanged(w: Int, h: Int, oldw: Int, oldh: Int) {
        super.onSizeChanged(w, h, oldw, oldh)
        rebuild()
    }

    override fun onDraw(canvas: Canvas) {
        for (control in controls) {
            val radius = if (control.shape == "round") control.rect.width() / 2f else 12f
            canvas.drawRoundRect(control.rect, radius, radius, fill)
            canvas.drawRoundRect(control.rect, radius, radius, outline)
            if (control.shape != "dpad") {
                canvas.drawText(
                    control.id.uppercase(),
                    control.rect.centerX(),
                    control.rect.centerY() + label.textSize / 3f,
                    label,
                )
            } else {
                // A cross through the box reads as a d-pad without any assets.
                canvas.drawLine(
                    control.rect.left, control.rect.centerY(),
                    control.rect.right, control.rect.centerY(), outline,
                )
                canvas.drawLine(
                    control.rect.centerX(), control.rect.top,
                    control.rect.centerX(), control.rect.bottom, outline,
                )
            }
        }
    }

    override fun onTouchEvent(event: MotionEvent): Boolean {
        var mask = 0
        var stylus: Pair<Int, Int>? = null

        for (index in 0 until event.pointerCount) {
            if (event.actionMasked == MotionEvent.ACTION_UP ||
                event.actionMasked == MotionEvent.ACTION_CANCEL
            ) {
                break  // every pointer is gone: release everything
            }
            val x = event.getX(index)
            val y = event.getY(index)

            val screen = bottomScreen
            if (screen != null && screen.contains(x, y)) {
                // The bottom screen is the lower half of the core's frame.
                val frameWidth = EmuCore.frameWidth().takeIf { it > 0 } ?: 256
                val frameHeight = EmuCore.frameHeight().takeIf { it > 0 } ?: 384
                val px = ((x - screen.left) / screen.width() * frameWidth).toInt()
                val py = (frameHeight / 2 +
                    (y - screen.top) / screen.height() * (frameHeight / 2)).toInt()
                stylus = px to py
                continue
            }

            for (control in controls) {
                if (!control.rect.contains(x, y)) continue
                if (control.shape == "dpad") {
                    mask = mask or dpadMask(control.rect, x, y)
                } else {
                    val bit = EmuCore.Button.of(control.id)
                    if (bit >= 0) mask = mask or (1 shl bit)
                }
            }
        }

        EmuCore.setButtons(mask)
        EmuCore.setPointer(stylus?.first ?: -1, stylus?.second ?: -1)
        return true
    }

    /** Which directions a touch inside the d-pad box means; diagonals included. */
    private fun dpadMask(rect: RectF, x: Float, y: Float): Int {
        var mask = 0
        val third = 1f / 3f
        val relativeX = (x - rect.left) / rect.width()
        val relativeY = (y - rect.top) / rect.height()
        if (relativeX < third) mask = mask or (1 shl EmuCore.Button.LEFT)
        if (relativeX > 1f - third) mask = mask or (1 shl EmuCore.Button.RIGHT)
        if (relativeY < third) mask = mask or (1 shl EmuCore.Button.UP)
        if (relativeY > 1f - third) mask = mask or (1 shl EmuCore.Button.DOWN)
        return mask
    }
}
