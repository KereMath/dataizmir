// Hide _id and other system columns in DataTables view
(function($) {
    $(document).ready(function() {
        // Wait for DataTable to be initialized
        setTimeout(function() {
            var table = $('#dtprv').DataTable();

            // Get all column headers
            var headers = table.columns().header();

            // Find and hide columns that start with underscore
            headers.each(function(index) {
                var columnText = $(this).text();
                if (columnText && (columnText.startsWith('_') || columnText === 'id' || columnText === 'ID')) {
                    // Hide the column
                    table.column(index).visible(false);
                    console.log('Hidden column: ' + columnText);
                }
            });
        }, 1000); // Wait 1 second for DataTable to fully load
    });
})(jQuery);