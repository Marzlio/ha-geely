# Geely

Home Assistant custom integration for a Geely that uses the international
Geely app. One config entry per account. Each car on the account becomes a
device with sensors, binary sensors and a device tracker.

This is unofficial and not made or endorsed by Geely.

It follows the shape of [ha-carlinko](https://github.com/Marzlio/ha-carlinko):
a config flow, a hub entry, and entities for the car. Sign-in is the email
code the Geely app uses. There is no password.

This version reads the car. It does not lock or unlock, start the climate,
or change charging. Those calls tell the car to do something, and they are
not included.

## Sign in

1. Copy `custom_components/geely` to `<config>/custom_components/geely`
   (or add this repository in HACS as an integration) and restart Home Assistant.
2. Settings → Devices & services → Add integration → **Geely**.
3. Enter the account email and the country. A South African car on the
   international app uses **United Kingdom**, which is the default.
4. Geely emails a code. Enter it once.

Request the code only once. A second request a couple of minutes later is
refused, and the first code then cannot be used.

Home Assistant stores the account session and each car's certificate in the
config entry, the same way CarLinko stores the account password, so a restart
does not ask for a new code. When Geely ends the session, Home Assistant asks
you to sign in again. That sends one new code.

Only one Geely session can be active. Signing in on the phone, or on the
local page at port 8791, ends this integration's session. Signing in here
ends the phone app's session.

## What you get

Battery, range, speed, average speed, odometer, temperatures, altitude,
direction, 12 V voltage, service distance and days, charge connection and
time to full, consumption when the car sends it, tyre pressures when the
car sends them, engine, park brake, seatbelts, doors, boot, bonnet, locks,
charging, and the last reported position.

A speed or outside temperature the car marks invalid is left empty. The raw
number is kept on the speed and outside-temperature entities as the
`reported` attribute, with `valid` false.

The position comes from the status the car already uploaded. This integration
does not ask the car to take a fresh GPS fix.

While the engine is on or the car is charging, it reads about every 45
seconds. While parked, about every 10 minutes. Both are on the integration's
Configure page. A parked car often repeats the same report. The read does
not wake it.

## Install with HACS

HACS → Integrations → Custom repositories → `https://github.com/Marzlio/ha-geely`
as an Integration. Install **Geely**, restart, then add the integration.

## Not included

The car's identifier, the session id, and the modem and SIM numbers are not
shown and are not written to the log. Kilowatts are only present in a status
reply while the car is charging or supplying power. Pack size is not in the
reply.
