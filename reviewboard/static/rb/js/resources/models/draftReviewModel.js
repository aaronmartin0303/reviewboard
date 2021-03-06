/*
 * Draft reviews.
 *
 * Draft reviews are more complicated than most objects. A draft may already
 * exist on the server, in which case we need to be able to get its ID. A
 * special resource exists at /reviews/draft/ which will redirect to the
 * existing draft if one exists, and return 404 if not.
 */
RB.DraftReview = RB.Review.extend(_.extend({
    /*
     * Publishes the review.
     *
     * Before publish, the "publishing" event will be triggered.
     *
     * After the publish has succeeded, the "publishing" event will be
     * triggered.
     */
    publish: function(options, context) {
        var error;

        options = options || {};

        error = _.isFunction(options.error)
                ? _.bind(options.error, context)
                : undefined;

        this.trigger('publishing');

        this.ready({
            ready: function() {
                this.set('public', true);
                this.save({
                    success: function() {
                        this.trigger('published');

                        if (_.isFunction(options.success)) {
                            options.success.call(context);
                        }
                    },
                    error: error
                }, this);
            },
            error: error
        }, this);
    }
}, RB.DraftResourceModelMixin));
