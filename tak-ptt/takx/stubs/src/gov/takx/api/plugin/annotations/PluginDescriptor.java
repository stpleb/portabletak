package gov.takx.api.plugin.annotations;
import java.lang.annotation.*;
@Target(ElementType.TYPE) @Retention(RetentionPolicy.RUNTIME)
public @interface PluginDescriptor { String name(); String description() default ""; String longDescription() default ""; }
