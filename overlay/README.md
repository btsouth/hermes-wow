# overlay (superseded)

The first attempt at this project: an Electron window pinned over a windowed game
through Hyprland, showing the same roster.

It was rejected as the primary path for a reason that has not changed: an overlay
can only ever sit on top of a *windowed* client, cannot be seen in exclusive full
screen, and the game can cover it. The addon draws inside the client instead, so
none of that applies, and the addon's badge and minimap button cover the same
"glance at it without alt-tabbing" need.

Kept because `hermes-wow overlay|toggle|show|hide|pin|unpin` still work, and
because a player who wants a badge visible without the game running can have one.
Not maintained alongside the addon: the roster, colors and layout there are frozen
where the addon left them.
