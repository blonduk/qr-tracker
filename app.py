from flask import Flask, request, send_file, session, redirect, render_template, abort
import qrcode
import qrcode.image.svg
import io

# ... rest of your app and setup ...

@app.route('/view-qr/<short_id>')
def view_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"

    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=20,  # Higher resolution
        border=4
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png')

@app.route('/download-qr/<short_id>.png')
def download_png_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    qr = qrcode.QRCode(
        version=1,
        error_correction=qrcode.constants.ERROR_CORRECT_H,
        box_size=20,
        border=4
    )
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    buf.seek(0)
    return send_file(buf, mimetype='image/png', as_attachment=True, download_name=f"glitchlink-{short_id}.png")

@app.route('/download-qr/<short_id>.svg')
def download_svg_qr(short_id):
    url = f"{request.host_url}track?id={short_id}"
    factory = qrcode.image.svg.SvgImage
    qr = qrcode.make(url, image_factory=factory)

    buf = io.BytesIO()
    qr.save(buf)
    buf.seek(0)
    return send_file(buf, mimetype='image/svg+xml', as_attachment=True, download_name=f"glitchlink-{short_id}.svg")
