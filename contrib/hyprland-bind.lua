-- Hermes WoW mode keybind (Omarchy / Hyprland, Lua config).
--
-- Append this to ~/.config/hypr/bindings.lua, then reload with `hyprctl reload`
-- (or run the file's command once to check it works first).
--
-- SUPER SHIFT H is free on a stock Omarchy install; if you have already bound
-- it, pick any other chord. The toggle starts the overlay when it is not up and
-- flips badge <-> board when it is.
--
-- Nothing here is required for the overlay itself: `hermes-wow toggle` works
-- from any shell, so a keybind is only the convenient front door.

-- hermes-wow:bindings:start
o.bind("SUPER + SHIFT + H", "Hermes WoW mode", "/home/bts/Projects/hermes-wow/bin/hermes-wow toggle")
-- hermes-wow:bindings:end
