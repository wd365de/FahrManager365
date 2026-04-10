self.addEventListener("push", function (event) {
    var data = {};
    if (event.data) {
        try {
            data = event.data.json();
        } catch (_error) {
            data = { body: event.data.text() };
        }
    }

    var title = data.title || "FahrManager";
    var options = {
        body: data.body || "Neue Benachrichtigung",
        icon: "/static/logo_fahrmanager360.png",
        badge: "/static/logo_fahrmanager360.png",
        data: {
            url: data.url || "/portal",
        },
    };

    event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener("notificationclick", function (event) {
    event.notification.close();
    var targetUrl = (event.notification.data && event.notification.data.url) || "/portal";

    event.waitUntil(
        clients.matchAll({ type: "window", includeUncontrolled: true }).then(function (windowClients) {
            for (var i = 0; i < windowClients.length; i += 1) {
                var client = windowClients[i];
                if (client.url.indexOf(targetUrl) !== -1 && "focus" in client) {
                    return client.focus();
                }
            }
            if (clients.openWindow) {
                return clients.openWindow(targetUrl);
            }
            return null;
        })
    );
});
